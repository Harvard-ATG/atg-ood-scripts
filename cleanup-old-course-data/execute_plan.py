#!/usr/bin/env python3
"""
Execute a backup/deletion plan produced by generate_plan.py.

For every item with status 'pending':
  1. Upload the directory tree to S3.
  2. Delete the local copy.
  3. Update the item's status in the plan file immediately, then sync
     both the plan file and the execution log back to S3.

The plan file and execution log are synced to fixed S3 keys after every
status change. If the run is interrupted, a second administrator can
resume from a different machine using --from-s3.

S3 layout
---------
  {s3_prefix}/plan/{plan_filename}            # updated after every item
  {s3_prefix}/logs/{log_filename}             # updated after every item
  {s3_prefix}/course_shared_folders/{name}/   # backed-up course folders
  {s3_prefix}/home/{username}/                # backed-up user home dirs

Usage
-----
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket --dry-run
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket --retry-failed

    # Resume an interrupted run from a different machine:
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket --from-s3
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket --from-s3 \\
        --s3-prefix backups/20240115
"""

import os
import re
import shutil
import logging
import argparse
import yaml
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# S3 sync helper
# ---------------------------------------------------------------------------

class S3Sync:
    """
    Keeps the plan file and execution log synced to fixed, well-known S3 keys
    so that a second administrator can resume a partial run from a different
    machine.

    The plan is the authoritative record of what has and hasn't been processed.
    The log provides a human-readable audit trail. Both are pushed to S3 after
    every status change so neither can fall more than one item behind.

    S3 layout (relative to s3_prefix):
        {s3_prefix}/plan/{plan_filename}
        {s3_prefix}/logs/{log_filename}
    """

    def __init__(
        self,
        s3_client,
        bucket:      str,
        plan_s3_key: str,
        log_s3_key:  str,
        logger:      logging.Logger,
    ):
        self.s3_client   = s3_client
        self.bucket      = bucket
        self.plan_s3_key = plan_s3_key
        self.log_s3_key  = log_s3_key
        self.logger      = logger

    def _upload(self, local_path: str, s3_key: str) -> None:
        """
        Upload a single file to S3. Logs a warning on failure rather than
        raising — a sync hiccup should never abort the backup/deletion work.
        """
        try:
            self.s3_client.upload_file(local_path, self.bucket, s3_key)
            self.logger.debug(
                f"Synced {local_path}  ->  s3://{self.bucket}/{s3_key}"
            )
        except (BotoCoreError, ClientError, OSError) as exc:
            self.logger.warning(
                f"Could not sync '{local_path}' to "
                f"s3://{self.bucket}/{s3_key}: {exc}"
            )

    def push_plan(self, local_path: str) -> None:
        """Upload the current plan file to its fixed S3 key."""
        self._upload(local_path, self.plan_s3_key)

    def push_log(self, local_path: str) -> None:
        """
        Flush all log handlers then upload the log file to its fixed S3 key.
        Flushing before uploading ensures the S3 copy captures the latest lines.
        """
        for handler in logging.getLogger("execute_plan").handlers:
            handler.flush()
        self._upload(local_path, self.log_s3_key)

    def push_both(self, plan_local: str, log_local: str) -> None:
        """Convenience: push plan then log in a single call."""
        self.push_plan(plan_local)
        self.push_log(log_local)

    # ---- Key derivation ----

    @staticmethod
    def plan_key(s3_prefix: str, plan_filename: str) -> str:
        return f"{s3_prefix}/plan/{plan_filename}"

    @staticmethod
    def log_key(s3_prefix: str, log_filename: str) -> str:
        return f"{s3_prefix}/logs/{log_filename}"

    # ---- Resume support: S3 -> local ----

    @staticmethod
    def download_plan(
        s3_client,
        bucket:     str,
        s3_key:     str,
        local_path: str,
        logger:     logging.Logger,
    ) -> bool:
        """
        Download a plan file from S3 to local_path, overwriting any existing
        local copy. The S3 version is always treated as authoritative since it
        reflects the most recently saved state of a prior run.
        Returns True on success, False on any error.
        """
        logger.info(
            f"Downloading plan from s3://{bucket}/{s3_key}  ->  {local_path}"
        )
        try:
            s3_client.download_file(bucket, s3_key, local_path)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                logger.error(
                    f"Plan not found: s3://{bucket}/{s3_key}\n"
                    f"  Verify that --s3-prefix matches the original run."
                )
            else:
                logger.error(f"S3 error downloading plan: {exc}")
            return False
        except (BotoCoreError, OSError) as exc:
            logger.error(f"Could not download plan: {exc}")
            return False


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_file: str) -> logging.Logger:
    logger = logging.getLogger("execute_plan")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


# ---------------------------------------------------------------------------
# S3 backup upload
# ---------------------------------------------------------------------------

def upload_directory_to_s3(
    s3_client,
    local_path: str,
    bucket:     str,
    s3_prefix:  str,
    logger:     logging.Logger,
) -> int:
    """
    Recursively upload every file under local_path to s3://bucket/s3_prefix/.
    Returns the number of files uploaded. Raises on the first error so the
    caller can mark the item as failed without deleting anything locally.
    """
    local_root = Path(local_path)
    count = 0

    for file_path in sorted(local_root.rglob("*")):
        if not file_path.is_file():
            continue

        relative = file_path.relative_to(local_root)
        s3_key   = f"{s3_prefix}/{relative}".replace("\\", "/")

        logger.debug(f"    uploading {file_path}  ->  s3://{bucket}/{s3_key}")
        s3_client.upload_file(str(file_path), bucket, s3_key)
        count += 1

    return count


# ---------------------------------------------------------------------------
# Plan I/O
# ---------------------------------------------------------------------------

def load_plan(plan_file: str) -> dict:
    with open(plan_file, "r") as fh:
        return yaml.safe_load(fh)


def save_plan(
    plan:      dict,
    plan_file: str,
    logger:    logging.Logger,
    syncer:    "S3Sync | None" = None,
    log_file:  "str | None"    = None,
) -> None:
    """
    Write the plan to disk, then immediately sync both the plan and the log
    to S3. S3 errors are warnings only — they never block the local write
    or interrupt the processing loop.
    """
    try:
        with open(plan_file, "w") as fh:
            yaml.dump(
                plan, fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
    except IOError as exc:
        logger.warning(f"Could not write plan file '{plan_file}': {exc}")
        return  # No point syncing if we couldn't write locally

    if syncer and log_file:
        syncer.push_both(plan_file, log_file)
    elif syncer:
        syncer.push_plan(plan_file)


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------

def process_item(
    item:      dict,
    item_type: str,          # "course_shared_folders" | "home"
    s3_client,
    bucket:    str,
    s3_prefix: str,
    plan:      dict,
    plan_file: str,
    dry_run:   bool,
    logger:    logging.Logger,
    syncer:    "S3Sync | None" = None,
    log_file:  "str | None"    = None,
) -> None:
    """
    Back up a single directory to S3 then delete the local copy.
    After every status change the plan is saved locally and synced to S3
    (along with the current log) so no progress is ever lost.
    """
    label = item.get("folder_name") or item.get("username") or item["path"]
    path  = item["path"]

    # ---- Skip already-resolved items ----
    current_status = item.get("status")
    if current_status == "completed":
        logger.info(f"  [skip] {label} — already completed")
        return
    if current_status == "failed":
        logger.info(f"  [skip] {label} — previously failed (use --retry-failed to retry)")
        return

    s3_dest = f"{s3_prefix}/{item_type}/{label}"
    s3_uri  = f"s3://{bucket}/{s3_dest}/"

    # ---- Dry-run ----
    if dry_run:
        logger.info(f"  [dry-run] would upload '{path}'  ->  {s3_uri}")
        logger.info(f"  [dry-run] would delete  '{path}'")
        return

    # ---- Sanity check ----
    if not os.path.exists(path):
        logger.warning(f"  [skip] {label} — path no longer exists: {path}")
        item["status"] = "skipped"
        item["note"]   = "Path not found at execution time"
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    logger.info(f"  Processing '{label}'")
    logger.info(f"    source : {path}")
    logger.info(f"    dest   : {s3_uri}")

    # ---- Upload ----
    try:
        count = upload_directory_to_s3(s3_client, path, bucket, s3_dest, logger)
        logger.info(f"    uploaded {count} file(s)")
    except (BotoCoreError, ClientError, OSError) as exc:
        logger.error(f"    Upload FAILED for '{label}': {exc}")
        item["status"]    = "failed"
        item["error"]     = str(exc)
        item["failed_at"] = datetime.now().isoformat(timespec="seconds")
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    # ---- Delete ----
    try:
        shutil.rmtree(path)
        logger.info(f"    deleted local copy")
    except OSError as exc:
        logger.error(f"    Deletion FAILED for '{label}': {exc}")
        item["status"]      = "failed"
        item["error"]       = f"Upload succeeded but deletion failed: {exc}"
        item["failed_at"]   = datetime.now().isoformat(timespec="seconds")
        item["s3_location"] = s3_uri
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    # ---- Success ----
    item["status"]       = "completed"
    item["completed_at"] = datetime.now().isoformat(timespec="seconds")
    item["s3_location"]  = s3_uri
    item.pop("error",     None)
    item.pop("failed_at", None)
    save_plan(plan, plan_file, logger, syncer, log_file)
    logger.info(f"    done")


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def execute_plan(
    plan_file:    str,
    s3_bucket:    str,
    s3_prefix:    str,
    dry_run:      bool,
    retry_failed: bool,
    log_file:     str,
    from_s3:      bool,
) -> None:

    logger = setup_logging(log_file)

    prefix = "[DRY RUN] " if dry_run else ""
    logger.info(f"{prefix}Plan file      : {plan_file}")
    logger.info(f"{prefix}S3 destination : s3://{s3_bucket}/{s3_prefix}/")
    logger.info(f"Log file       : {log_file}")

    # ---- Derive fixed S3 keys for the plan and log ----
    plan_filename = os.path.basename(plan_file)
    log_filename  = os.path.basename(log_file)
    plan_s3_key   = S3Sync.plan_key(s3_prefix, plan_filename)
    log_s3_key    = S3Sync.log_key(s3_prefix, log_filename)

    logger.info(f"Plan in S3     : s3://{s3_bucket}/{plan_s3_key}")
    logger.info(f"Log in S3      : s3://{s3_bucket}/{log_s3_key}")

    s3_client = boto3.client("s3")

    # ---- Optionally pull the latest plan from S3 before starting ----
    if from_s3:
        logger.info("--from-s3 specified: downloading authoritative plan from S3")
        success = S3Sync.download_plan(
            s3_client, s3_bucket, plan_s3_key, plan_file, logger
        )
        if not success:
            logger.error("Cannot proceed without a plan file. Aborting.")
            return

    # ---- Build syncer — disabled entirely in dry-run mode ----
    syncer = None if dry_run else S3Sync(
        s3_client, s3_bucket, plan_s3_key, log_s3_key, logger
    )

    plan = load_plan(plan_file)

    # ---- Reset failed items if requested ----
    if retry_failed and not dry_run:
        reset_count = 0
        for section in ("course_shared_folders", "user_home_directories"):
            for item in plan.get(section, {}).get("to_backup", []):
                if item.get("status") == "failed":
                    item["status"] = "pending"
                    item.pop("error",     None)
                    item.pop("failed_at", None)
                    reset_count += 1
        if reset_count:
            logger.info(f"Reset {reset_count} previously failed item(s) to pending")

    # ---- Initial sync: push plan and log to S3 before any work begins ----
    # This records the starting state and makes the log file immediately
    # visible in S3 even if the run is interrupted after the first item.
    if syncer:
        logger.info("Performing initial S3 sync...")
        syncer.push_both(plan_file, log_file)

    course_items = plan.get("course_shared_folders", {}).get("to_backup", [])
    user_items   = plan.get("user_home_directories", {}).get("to_backup", [])

    # ---- Course shared folders ----
    logger.info(f"\n--- Course Shared Folders ({len(course_items)} item(s) in plan) ---")
    for item in course_items:
        process_item(
            item, "course_shared_folders",
            s3_client, s3_bucket, s3_prefix,
            plan, plan_file, dry_run, logger, syncer, log_file,
        )

    # ---- User home directories ----
    logger.info(f"\n--- User Home Directories ({len(user_items)} item(s) in plan) ---")
    for item in user_items:
        process_item(
            item, "home",
            s3_client, s3_bucket, s3_prefix,
            plan, plan_file, dry_run, logger, syncer, log_file,
        )

    # ---- Summary ----
    def tally(items: list[dict], status: str) -> int:
        return sum(1 for i in items if i.get("status") == status)

    logger.info("\n--- Summary ---")
    for label, items in (
        ("Course folders  ", course_items),
        ("User directories", user_items),
    ):
        logger.info(
            f"  {label}:  "
            f"completed={tally(items, 'completed')}  "
            f"failed={tally(items, 'failed')}  "
            f"skipped={tally(items, 'skipped')}  "
            f"pending={tally(items, 'pending')}"
        )

    # ---- Final stamp and sync ----
    if not dry_run:
        plan["metadata"]["last_executed_at"] = datetime.now().isoformat(timespec="seconds")
        save_plan(plan, plan_file, logger, syncer, log_file)
        logger.info(f"\nPlan saved locally : {plan_file}")
        logger.info(f"Plan in S3         : s3://{s3_bucket}/{plan_s3_key}")
        logger.info(f"Log in S3          : s3://{s3_bucket}/{log_s3_key}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute a backup/deletion plan: upload pending items to S3, "
            "then delete the local copies. The plan file and execution log "
            "are continuously synced to S3 after every item so that an "
            "interrupted run can be resumed from a different machine."
        )
    )
    parser.add_argument(
        "plan_file",
        help=(
            "Local path for the YAML plan file. "
            "When --from-s3 is used, this is the download destination and "
            "any existing local file will be overwritten with the S3 version."
        ),
    )
    parser.add_argument(
        "s3_bucket",
        help="S3 bucket name",
    )
    parser.add_argument(
        "--s3-prefix",
        default=None,
        help=(
            "S3 key prefix for all uploads and sync files "
            "(default: derived from the date in the plan filename). "
            "When using --from-s3 this must match the prefix used in the "
            "original run, otherwise the wrong plan will be downloaded."
        ),
    )
    parser.add_argument(
        "--from-s3",
        action="store_true",
        help=(
            "Download the plan from S3 before starting. "
            "Use this to resume an interrupted run from a different machine. "
            "The plan_file argument is used as the local download destination."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without uploading, deleting, or syncing to S3",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reset previously failed items to pending so they are retried",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help=(
            "Path for the execution log "
            "(default: backup_execution_YYYYMMDD_HHMMSS.log). "
            "Each resumed run gets its own log file; all are uploaded to "
            "{s3_prefix}/logs/ for auditing."
        ),
    )

    args = parser.parse_args()

    # ---- Derive s3_prefix ----
    # When resuming with --from-s3 we need the prefix before the plan is
    # available locally. Extract the date from the plan filename first since
    # that's reliable; fall back to reading the plan, then to today's date.
    s3_prefix = args.s3_prefix
    if s3_prefix is None:
        match = re.search(r"(\d{8})", os.path.basename(args.plan_file))
        if match:
            s3_prefix = f"backups/{match.group(1)}"
        elif not args.from_s3:
            try:
                meta         = load_plan(args.plan_file).get("metadata", {})
                generated_at = meta.get("generated_at", datetime.now().isoformat())
                date_slug    = generated_at[:10].replace("-", "")
                s3_prefix    = f"backups/{date_slug}"
            except Exception:
                s3_prefix = f"backups/{datetime.now().strftime('%Y%m%d')}"
        else:
            s3_prefix = f"backups/{datetime.now().strftime('%Y%m%d')}"
            print(
                f"Warning: could not derive S3 prefix from plan filename. "
                f"Defaulting to '{s3_prefix}'. "
                f"Pass --s3-prefix explicitly if this does not match the original run."
            )

    log_file = (
        args.log_file
        or f"backup_execution_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )

    execute_plan(
        plan_file=args.plan_file,
        s3_bucket=args.s3_bucket,
        s3_prefix=s3_prefix,
        dry_run=args.dry_run,
        retry_failed=args.retry_failed,
        log_file=log_file,
        from_s3=args.from_s3,
    )


if __name__ == "__main__":
    main()
