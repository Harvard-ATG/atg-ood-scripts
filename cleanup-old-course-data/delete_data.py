#!/usr/bin/env python3
"""
Delete local directories that have been successfully backed up to S3.

Only items with status 'backed_up' are processed. Before each deletion the
script verifies that the S3 archive exists and is non-empty, refusing to
delete the local copy if it cannot be confirmed.

Items in any other status are skipped:
  'pending'   -> not yet backed up; run backup_data.py first
  'completed' -> already deleted
  'failed'    -> backup failed; re-run backup_data.py --retry-failed
  'skipped'   -> was skipped during backup

Usage
-----
    # Pull the latest plan from S3 then delete (recommended):
    python delete_data.py backup_plan_20240115_103000.yml my-s3-bucket --from-s3

    # Dry run first to confirm what will be deleted:
    python delete_data.py backup_plan_20240115_103000.yml my-s3-bucket --from-s3 --dry-run

    # Retry local deletions that failed in a previous run (e.g. permission errors):
    python delete_data.py backup_plan_20240115_103000.yml my-s3-bucket \\
        --from-s3 --retry-failed
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


# ---------------------------------------------------------------------------
# S3 sync helper
# ---------------------------------------------------------------------------

class S3Sync:
    """
    Keeps the plan file and execution log synced to fixed, well-known S3 keys.
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
        self._upload(local_path, self.plan_s3_key)

    def push_log(self, local_path: str) -> None:
        for handler in logging.getLogger("delete_data").handlers:
            handler.flush()
        self._upload(local_path, self.log_s3_key)

    def push_both(self, plan_local: str, log_local: str) -> None:
        self.push_plan(plan_local)
        self.push_log(log_local)

    @staticmethod
    def plan_key(s3_prefix: str, plan_filename: str) -> str:
        return f"{s3_prefix}/plan/{plan_filename}"

    @staticmethod
    def log_key(s3_prefix: str, log_filename: str) -> str:
        return f"{s3_prefix}/logs/{log_filename}"

    @staticmethod
    def download_plan(
        s3_client,
        bucket:     str,
        s3_key:     str,
        local_path: str,
        logger:     logging.Logger,
    ) -> bool:
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
                    f"  Verify that --s3-prefix matches the backup run."
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
    logger = logging.getLogger("delete_data")
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
        return

    if syncer and log_file:
        syncer.push_both(plan_file, log_file)
    elif syncer:
        syncer.push_plan(plan_file)


# ---------------------------------------------------------------------------
# S3 archive verification
# ---------------------------------------------------------------------------

def verify_s3_archive(
    s3_client,
    bucket:  str,
    s3_key:  str,
    logger:  logging.Logger,
) -> bool:
    """
    Confirm the S3 archive exists and has a non-zero size before any local
    data is deleted. Returns True if the object is present and non-empty.

    This is the primary safeguard against data loss: if the archive cannot
    be confirmed in S3, the deletion is refused and the item is marked failed.
    """
    try:
        response  = s3_client.head_object(Bucket=bucket, Key=s3_key)
        size_mb   = response.get("ContentLength", 0) / (1024 * 1024)
        if size_mb == 0:
            logger.error(
                f"    S3 object exists but reports zero size: "
                f"s3://{bucket}/{s3_key}"
            )
            return False
        logger.debug(
            f"    Verified: s3://{bucket}/{s3_key} ({size_mb:.1f} MB)"
        )
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey"):
            logger.error(f"    S3 archive not found: s3://{bucket}/{s3_key}")
        else:
            logger.error(f"    Error checking S3 archive: {exc}")
        return False
    except (BotoCoreError, OSError) as exc:
        logger.error(f"    Error verifying S3 archive: {exc}")
        return False


# ---------------------------------------------------------------------------
# S3 key resolution
# ---------------------------------------------------------------------------

def resolve_s3_key(item: dict, bucket: str, logger: logging.Logger) -> "str | None":
    """
    Return the S3 key for this item's archive.

    Prefers the 's3_key' field written by backup_data.py. Falls back to
    parsing 's3_location' for compatibility with plans produced by older
    versions of the scripts.
    """
    s3_key = item.get("s3_key")
    if s3_key:
        return s3_key

    # Fallback: parse "s3://bucket/some/key.tar.gz"
    s3_location = item.get("s3_location", "")
    prefix      = f"s3://{bucket}/"
    if s3_location.startswith(prefix):
        derived = s3_location[len(prefix):]
        logger.debug(f"    Derived s3_key from s3_location: {derived}")
        return derived

    logger.error(
        f"    Could not determine S3 key — "
        f"'s3_key' is missing and 's3_location' is unreadable: "
        f"'{s3_location}'"
    )
    return None


# ---------------------------------------------------------------------------
# Pre-flight status report
# ---------------------------------------------------------------------------

def preflight_report(
    course_items: list[dict],
    user_items:   list[dict],
    logger:       logging.Logger,
) -> bool:
    """
    Log a breakdown of item statuses before deletion begins.
    Returns True if there is at least one 'backed_up' item to process.
    """
    def tally(items: list[dict], status: str) -> int:
        return sum(1 for i in items if i.get("status") == status)

    all_items = course_items + user_items
    statuses  = ["backed_up", "pending", "failed", "completed", "skipped"]

    logger.info("\n--- Pre-flight Status ---")
    for label, items in (
        ("Course folders  ", course_items),
        ("User directories", user_items),
    ):
        counts = "  ".join(f"{s}={tally(items, s)}" for s in statuses)
        logger.info(f"  {label}: {counts}")

    pending_count = tally(all_items, "pending")
    if pending_count:
        logger.warning(
            f"\n  WARNING: {pending_count} item(s) still have status 'pending' "
            f"and have not been backed up. They will not be deleted. "
            f"Run backup_data.py to back them up first."
        )

    failed_count = tally(all_items, "failed")
    if failed_count:
        logger.warning(
            f"  WARNING: {failed_count} item(s) have status 'failed'. "
            f"They will not be deleted. "
            f"Review the plan and logs, then re-run backup_data.py --retry-failed."
        )

    ready_count = tally(all_items, "backed_up")
    if not ready_count:
        logger.info("\n  No items with status 'backed_up' found. Nothing to delete.")
        return False

    logger.info(f"\n  {ready_count} item(s) confirmed 'backed_up' and ready for deletion.")
    return True


# ---------------------------------------------------------------------------
# Per-item processing
# ---------------------------------------------------------------------------

def delete_item(
    item:      dict,
    s3_client,
    bucket:    str,
    plan:      dict,
    plan_file: str,
    dry_run:   bool,
    logger:    logging.Logger,
    syncer:    "S3Sync | None" = None,
    log_file:  "str | None"    = None,
) -> None:
    """
    Delete a local directory that has been confirmed as backed up to S3.

    Only processes items with status 'backed_up'. Verifies the S3 archive
    exists and is non-empty before issuing any deletion. The local copy is
    never deleted if the S3 verification fails.
    """
    label  = item.get("folder_name") or item.get("username") or item["path"]
    path   = item["path"]
    status = item.get("status")

    # ---- Guard: only act on backed_up items ----
    if status != "backed_up":
        messages = {
            "completed": (logging.INFO,    "already deleted"),
            "pending":   (logging.WARNING, "not yet backed up — run backup_data.py first"),
            "failed":    (logging.WARNING, "backup failed — re-run backup_data.py --retry-failed"),
            "skipped":   (logging.INFO,    "was skipped during backup"),
        }
        level, reason = messages.get(status, (logging.WARNING, f"unexpected status '{status}'"))
        logger.log(level, f"  [skip] {label} — {reason}")
        return

    s3_key = resolve_s3_key(item, bucket, logger)
    if not s3_key:
        # Error already logged by resolve_s3_key
        return

    s3_uri = f"s3://{bucket}/{s3_key}"

    if dry_run:
        logger.info(f"  [dry-run] would verify   {s3_uri}")
        logger.info(f"  [dry-run] would delete   '{path}'")
        return

    # Handle the case where the local path is already gone
    if not os.path.exists(path):
        logger.warning(f"  [skip] {label} — local path no longer exists: {path}")
        item["status"]       = "completed"
        item["completed_at"] = datetime.now().isoformat(timespec="seconds")
        item["note"]         = "Local path already absent at deletion time"
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    logger.info(f"  Deleting '{label}'")
    logger.info(f"    local   : {path}")
    logger.info(f"    archive : {s3_uri}")

    # ---- Verify S3 archive before touching local data ----
    logger.info(f"    Verifying S3 archive...")
    if not verify_s3_archive(s3_client, bucket, s3_key, logger):
        logger.error(
            f"    Refusing to delete '{path}' — "
            f"S3 archive could not be confirmed. No local data was removed."
        )
        item["status"]    = "failed"
        item["error"]     = "S3 archive could not be verified before deletion"
        item["failed_at"] = datetime.now().isoformat(timespec="seconds")
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    # ---- Delete local copy ----
    try:
        shutil.rmtree(path)
        logger.info(f"    deleted local copy")
    except OSError as exc:
        logger.error(f"    Deletion FAILED for '{label}': {exc}")
        item["status"]    = "failed"
        item["error"]     = str(exc)
        item["failed_at"] = datetime.now().isoformat(timespec="seconds")
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    item["status"]       = "completed"
    item["completed_at"] = datetime.now().isoformat(timespec="seconds")
    item.pop("error",     None)
    item.pop("failed_at", None)
    save_plan(plan, plan_file, logger, syncer, log_file)
    logger.info(f"    done")


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def run_deletion(
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
    logger.info(f"{prefix}Plan file  : {plan_file}")
    logger.info(f"{prefix}S3 bucket  : s3://{s3_bucket}/{s3_prefix}/")
    logger.info(f"Log file   : {log_file}")

    plan_filename = os.path.basename(plan_file)
    log_filename  = os.path.basename(log_file)
    plan_s3_key   = S3Sync.plan_key(s3_prefix, plan_filename)
    log_s3_key    = S3Sync.log_key(s3_prefix, log_filename)

    logger.info(f"Plan in S3 : s3://{s3_bucket}/{plan_s3_key}")
    logger.info(f"Log in S3  : s3://{s3_bucket}/{log_s3_key}")

    s3_client = boto3.client("s3")

    if from_s3:
        logger.info("--from-s3 specified: downloading authoritative plan from S3")
        success = S3Sync.download_plan(
            s3_client, s3_bucket, plan_s3_key, plan_file, logger
        )
        if not success:
            logger.error("Cannot proceed without a plan file. Aborting.")
            return

    syncer = None if dry_run else S3Sync(
        s3_client, s3_bucket, plan_s3_key, log_s3_key, logger
    )

    plan = load_plan(plan_file)

    if retry_failed and not dry_run:
        reset_count = 0
        for section in ("course_shared_folders", "user_home_directories"):
            for item in plan.get(section, {}).get("to_backup", []):
                # Only reset items where the backup succeeded (s3_location is
                # set) but the local deletion failed. Items with no s3_location
                # failed during backup and belong to backup_data.py --retry-failed.
                if item.get("status") == "failed" and item.get("s3_location"):
                    item["status"] = "backed_up"
                    item.pop("error",     None)
                    item.pop("failed_at", None)
                    reset_count += 1
        if reset_count:
            logger.info(
                f"Reset {reset_count} previously failed deletion(s) to backed_up"
            )

    if syncer:
        syncer.push_both(plan_file, log_file)

    course_items = plan.get("course_shared_folders", {}).get("to_backup", [])
    user_items   = plan.get("user_home_directories", {}).get("to_backup", [])

    # Show pre-flight breakdown and exit early if nothing to do
    has_work = preflight_report(course_items, user_items, logger)
    if not has_work:
        if syncer:
            syncer.push_both(plan_file, log_file)
        return

    logger.info(f"\n--- Course Shared Folders ({len(course_items)} item(s) in plan) ---")
    for item in course_items:
        delete_item(
            item, s3_client, s3_bucket,
            plan, plan_file, dry_run, logger, syncer, log_file,
        )

    logger.info(f"\n--- User Home Directories ({len(user_items)} item(s) in plan) ---")
    for item in user_items:
        delete_item(
            item, s3_client, s3_bucket,
            plan, plan_file, dry_run, logger, syncer, log_file,
        )

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
            f"backed_up={tally(items, 'backed_up')}  "
            f"pending={tally(items, 'pending')}  "
            f"skipped={tally(items, 'skipped')}"
        )

    if not dry_run:
        plan["metadata"]["last_deletion_at"] = datetime.now().isoformat(timespec="seconds")
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
            "Delete local directories that have been backed up to S3 by "
            "backup_data.py. Only items with status 'backed_up' are processed. "
            "The S3 archive is verified before each deletion."
        )
    )
    parser.add_argument(
        "plan_file",
        help=(
            "Local path for the YAML plan file. When --from-s3 is used, "
            "this is the download destination and any existing local file "
            "will be overwritten with the S3 version."
        ),
    )
    parser.add_argument("s3_bucket", help="S3 bucket name")
    parser.add_argument(
        "--s3-prefix",
        default=None,
        help=(
            "S3 key prefix (default: derived from the date in the plan "
            "filename). Must match the prefix used during the backup run."
        ),
    )
    parser.add_argument(
        "--from-s3",
        action="store_true",
        help=(
            "Download the latest plan from S3 before proceeding. "
            "Strongly recommended to ensure you are working from the most "
            "current backup state, especially if a different machine or "
            "administrator ran backup_data.py."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without deleting anything or syncing to S3",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Reset items that failed during a previous deletion attempt back "
            "to 'backed_up' so they are retried. Only affects items where the "
            "backup succeeded but deletion failed. Items where backup itself "
            "failed are not affected — use backup_data.py --retry-failed for those."
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path for the execution log (default: delete_YYYYMMDD_HHMMSS.log)",
    )

    args = parser.parse_args()

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
                f"Pass --s3-prefix explicitly if this does not match the backup run."
            )

    log_file = (
        args.log_file
        or f"delete_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )

    run_deletion(
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
