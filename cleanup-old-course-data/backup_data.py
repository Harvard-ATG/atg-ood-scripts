#!/usr/bin/env python3
"""
Back up directories listed in a plan file to S3 as compressed .tar.gz archives.

For every item with status 'pending':
  1. Compress the directory to a temporary .tar.gz archive.
  2. Upload the archive to S3 using the Glacier Instant Retrieval storage class.
  3. Mark the item 'backed_up' in the plan file.
  4. Sync the updated plan and execution log to S3.

Local directories are NOT deleted by this script. After reviewing the backup
results in the plan file, run delete_data.py to remove directories that are
marked 'backed_up'.

S3 layout
---------
  {s3_prefix}/plan/{plan_filename}                       <- standard storage
  {s3_prefix}/logs/{log_filename}                        <- standard storage
  {s3_prefix}/course_shared_folders/{name}.tar.gz        <- Glacier IR
  {s3_prefix}/home/{username}.tar.gz                     <- Glacier IR

Usage
-----
    python backup_data.py backup_plan_20240115_103000.yml my-s3-bucket
    python backup_data.py backup_plan_20240115_103000.yml my-s3-bucket --dry-run

    # Retry items that failed during a previous backup run:
    python backup_data.py backup_plan_20240115_103000.yml my-s3-bucket --retry-failed

    # Resume an interrupted run from a different machine:
    python backup_data.py backup_plan_20240115_103000.yml my-s3-bucket --from-s3 \\
        --s3-prefix backups/20240115
"""

import os
import re
import tarfile
import tempfile
import logging
import argparse
import yaml
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from datetime import datetime
from pathlib import Path


# Storage class used for all backup archives.
# Plan files and logs intentionally use the default (standard) storage class
# so they remain immediately readable at any point during or after a run.
BACKUP_STORAGE_CLASS = "GLACIER_IR"


# ---------------------------------------------------------------------------
# S3 sync helper
# ---------------------------------------------------------------------------

class S3Sync:
    """
    Keeps the plan file and execution log synced to fixed, well-known S3 keys
    so that a second administrator can resume a partial run from a different
    machine.

    Plan files and logs use standard storage since they must be immediately
    readable at any point. Only backup archives use Glacier Instant Retrieval.
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
        Upload a single file to S3 on standard storage. Logs a warning on
        failure rather than raising — a sync hiccup should never abort the
        backup work.
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
        self._upload(local_path, self.plan_s3_key)

    def push_log(self, local_path: str) -> None:
        """Flush handlers before uploading so the S3 copy is fully up to date."""
        for handler in logging.getLogger("backup_data").handlers:
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
        """
        Download a plan file from S3, overwriting any local copy.
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
    logger = logging.getLogger("backup_data")
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
# Compression and S3 upload
# ---------------------------------------------------------------------------

def compress_and_upload_to_s3(
    s3_client,
    local_path: str,
    bucket:     str,
    s3_key:     str,
    logger:     logging.Logger,
) -> tuple[int, float]:
    """
    Compress the directory at local_path to a temporary .tar.gz archive and
    upload it to S3 using Glacier Instant Retrieval.

    The archive is written to a temporary directory that is cleaned up after
    the upload regardless of success or failure. Compression requires temporary
    disk space comparable to the size of the source directory.

    Returns (file_count, compressed_size_mb).
    Raises on any compression or S3 error so the caller can mark the item
    failed without deleting the local directory.
    """
    folder_name = Path(local_path).name
    file_count  = sum(1 for p in Path(local_path).rglob("*") if p.is_file())

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = os.path.join(tmp_dir, f"{folder_name}.tar.gz")

        logger.debug(f"    Compressing {file_count} file(s) from '{local_path}'")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(local_path, arcname=folder_name)

        compressed_mb = os.path.getsize(archive_path) / (1024 * 1024)
        logger.debug(f"    Compressed size: {compressed_mb:.1f} MB")
        logger.debug(
            f"    Uploading '{archive_path}'  ->  s3://{bucket}/{s3_key} "
            f"[{BACKUP_STORAGE_CLASS}]"
        )

        s3_client.upload_file(
            archive_path,
            bucket,
            s3_key,
            ExtraArgs={"StorageClass": BACKUP_STORAGE_CLASS},
        )

    return file_count, compressed_mb


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
# Per-item processing
# ---------------------------------------------------------------------------

def backup_item(
    item:      dict,
    item_type: str,           # "course_shared_folders" | "home"
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
    Compress and upload a single directory to S3.
    Marks the item 'backed_up' on success. Does NOT delete the local copy.
    """
    label = item.get("folder_name") or item.get("username") or item["path"]
    path  = item["path"]

    current_status = item.get("status")
    if current_status == "backed_up":
        logger.info(f"  [skip] {label} — already backed up")
        return
    if current_status == "completed":
        logger.info(f"  [skip] {label} — already completed (backed up and deleted)")
        return
    if current_status == "failed":
        logger.info(
            f"  [skip] {label} — previously failed "
            f"(use --retry-failed to retry)"
        )
        return

    s3_key = f"{s3_prefix}/{item_type}/{label}.tar.gz"
    s3_uri = f"s3://{bucket}/{s3_key}"

    if dry_run:
        logger.info(f"  [dry-run] would compress and upload '{path}'")
        logger.info(f"  [dry-run]   ->  {s3_uri}  [{BACKUP_STORAGE_CLASS}]")
        return

    if not os.path.exists(path):
        logger.warning(f"  [skip] {label} — path no longer exists: {path}")
        item["status"] = "skipped"
        item["note"]   = "Path not found at execution time"
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    logger.info(f"  Backing up '{label}'")
    logger.info(f"    source  : {path}")
    logger.info(f"    dest    : {s3_uri}")
    logger.info(f"    storage : {BACKUP_STORAGE_CLASS}")

    try:
        file_count, compressed_mb = compress_and_upload_to_s3(
            s3_client, path, bucket, s3_key, logger
        )
        logger.info(
            f"    uploaded {file_count} file(s) "
            f"as {compressed_mb:.1f} MB archive"
        )
    except (BotoCoreError, ClientError, OSError, tarfile.TarError) as exc:
        logger.error(f"    Backup FAILED for '{label}': {exc}")
        item["status"]    = "failed"
        item["error"]     = str(exc)
        item["failed_at"] = datetime.now().isoformat(timespec="seconds")
        save_plan(plan, plan_file, logger, syncer, log_file)
        return

    item["status"]        = "backed_up"
    item["backed_up_at"]  = datetime.now().isoformat(timespec="seconds")
    item["s3_location"]   = s3_uri
    item["s3_key"]        = s3_key
    item["storage_class"] = BACKUP_STORAGE_CLASS
    item.pop("error",     None)
    item.pop("failed_at", None)
    save_plan(plan, plan_file, logger, syncer, log_file)
    logger.info(f"    done — local copy still in place for review")


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def run_backup(
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
    logger.info(f"{prefix}Storage class  : {BACKUP_STORAGE_CLASS} (backup archives)")
    logger.info(f"Log file       : {log_file}")

    plan_filename = os.path.basename(plan_file)
    log_filename  = os.path.basename(log_file)
    plan_s3_key   = S3Sync.plan_key(s3_prefix, plan_filename)
    log_s3_key    = S3Sync.log_key(s3_prefix, log_filename)

    logger.info(f"Plan in S3     : s3://{s3_bucket}/{plan_s3_key}")
    logger.info(f"Log in S3      : s3://{s3_bucket}/{log_s3_key}")

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
                # Only reset items where the backup itself failed (no s3_location
                # means the upload never completed). Items where backup succeeded
                # but deletion later failed (s3_location is set) are handled by
                # delete_data.py --retry-failed.
                if item.get("status") == "failed" and not item.get("s3_location"):
                    item["status"] = "pending"
                    item.pop("error",     None)
                    item.pop("failed_at", None)
                    reset_count += 1
        if reset_count:
            logger.info(f"Reset {reset_count} previously failed backup(s) to pending")

    if syncer:
        logger.info("Performing initial S3 sync...")
        syncer.push_both(plan_file, log_file)

    course_items = plan.get("course_shared_folders", {}).get("to_backup", [])
    user_items   = plan.get("user_home_directories", {}).get("to_backup", [])

    logger.info(f"\n--- Course Shared Folders ({len(course_items)} item(s) in plan) ---")
    for item in course_items:
        backup_item(
            item, "course_shared_folders",
            s3_client, s3_bucket, s3_prefix,
            plan, plan_file, dry_run, logger, syncer, log_file,
        )

    logger.info(f"\n--- User Home Directories ({len(user_items)} item(s) in plan) ---")
    for item in user_items:
        backup_item(
            item, "home",
            s3_client, s3_bucket, s3_prefix,
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
            f"backed_up={tally(items, 'backed_up')}  "
            f"failed={tally(items, 'failed')}  "
            f"skipped={tally(items, 'skipped')}  "
            f"pending={tally(items, 'pending')}"
        )

    if not dry_run:
        plan["metadata"]["last_backup_at"] = datetime.now().isoformat(timespec="seconds")
        save_plan(plan, plan_file, logger, syncer, log_file)
        logger.info(f"\nPlan saved locally : {plan_file}")
        logger.info(f"Plan in S3         : s3://{s3_bucket}/{plan_s3_key}")
        logger.info(f"Log in S3          : s3://{s3_bucket}/{log_s3_key}")

        backed_up_count = sum(tally(i, "backed_up") for i in (course_items, user_items))
        pending_count   = sum(tally(i, "pending")   for i in (course_items, user_items))
        failed_count    = sum(tally(i, "failed")     for i in (course_items, user_items))

        if pending_count or failed_count:
            logger.info(
                f"\n  {pending_count} pending and {failed_count} failed item(s) remain. "
                f"Resolve these before proceeding to deletion."
            )
        if backed_up_count:
            logger.info(
                f"\n  {backed_up_count} item(s) are ready for review. "
                f"Once satisfied, delete local copies with:\n"
                f"    python delete_data.py {plan_file} {s3_bucket} --from-s3"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Back up pending directories from a plan file to S3 as .tar.gz "
            "archives using Glacier Instant Retrieval storage. Does not delete "
            "local copies — run delete_data.py after reviewing the backups."
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
            "S3 key prefix for all uploads and sync files "
            "(default: derived from the date in the plan filename)"
        ),
    )
    parser.add_argument(
        "--from-s3",
        action="store_true",
        help="Download the plan from S3 before starting, overwriting any local copy",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without compressing, uploading, or syncing to S3",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help=(
            "Reset items that failed during a previous backup run back to "
            "pending so they are retried. Does not affect items where backup "
            "succeeded but deletion later failed — use delete_data.py "
            "--retry-failed for those."
        ),
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Path for the execution log (default: backup_YYYYMMDD_HHMMSS.log)",
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
                f"Pass --s3-prefix explicitly if this does not match the original run."
            )

    log_file = (
        args.log_file
        or f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )

    run_backup(
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
