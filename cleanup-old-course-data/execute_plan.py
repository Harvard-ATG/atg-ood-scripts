#!/usr/bin/env python3
"""
Execute a backup/deletion plan produced by generate_plan.py.

For every item with status 'pending':
  1. Upload the directory tree to S3.
  2. Delete the local copy.
  3. Update the item's status in the plan file immediately.

The plan file is updated after each item so a partial run can be safely
resumed — already-completed items are skipped automatically.

Usage:
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket --dry-run
    python execute_plan.py backup_plan_20240115_103000.yml my-s3-bucket --retry-failed
"""

import os
import shutil
import logging
import argparse
import yaml
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from datetime import datetime
from pathlib import Path


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
# S3 upload
# ---------------------------------------------------------------------------

def upload_directory_to_s3(
    s3_client,
    local_path: str,
    bucket: str,
    s3_prefix: str,
    logger: logging.Logger,
) -> int:
    """
    Recursively upload every file under local_path to s3://bucket/s3_prefix/.
    Directory structure is preserved relative to local_path itself.
    Returns the number of files uploaded.
    Raises on the first S3 or I/O error so the caller can mark the item failed.
    """
    local_root = Path(local_path)
    count = 0

    for file_path in sorted(local_root.rglob("*")):
        if not file_path.is_file():
            continue

        relative   = file_path.relative_to(local_root)
        s3_key     = f"{s3_prefix}/{relative}".replace("\\", "/")

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


def save_plan(plan: dict, plan_file: str, logger: logging.Logger) -> None:
    """Persist the updated plan back to disk."""
    try:
        with open(plan_file, "w") as fh:
            yaml.dump(
                plan, fh,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )
    except IOError as exc:
        logger.warning(f"Could not save plan file '{plan_file}': {exc}")


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
) -> None:
    """
    Back up a single directory to S3 then delete the local copy.
    Writes status back into the item dict and saves the plan after every
    state change so progress survives an interrupted run.
    """
    label = item.get("folder_name") or item.get("username") or item["path"]
    path  = item["path"]

    # ---- Guard: skip items that are already resolved ----
    current_status = item.get("status")
    if current_status == "completed":
        logger.info(f"  [skip] {label} — already completed")
        return
    if current_status == "failed":
        logger.info(f"  [skip] {label} — previously failed (use --retry-failed to retry)")
        return

    s3_dest = f"{s3_prefix}/{item_type}/{label}"
    s3_uri  = f"s3://{bucket}/{s3_dest}/"

    # ---- Dry-run mode ----
    if dry_run:
        logger.info(f"  [dry-run] would upload '{path}'  ->  {s3_uri}")
        logger.info(f"  [dry-run] would delete  '{path}'")
        return

    # ---- Sanity check ----
    if not os.path.exists(path):
        logger.warning(f"  [skip] {label} — path no longer exists: {path}")
        item["status"] = "skipped"
        item["note"]   = "Path not found at execution time"
        save_plan(plan, plan_file, logger)
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
        save_plan(plan, plan_file, logger)
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
        item["s3_location"] = s3_uri          # record where the upload landed
        save_plan(plan, plan_file, logger)
        return

    # ---- Success ----
    item["status"]       = "completed"
    item["completed_at"] = datetime.now().isoformat(timespec="seconds")
    item["s3_location"]  = s3_uri
    item.pop("error",     None)
    item.pop("failed_at", None)
    save_plan(plan, plan_file, logger)
    logger.info(f"    done")


# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def execute_plan(
    plan_file:     str,
    s3_bucket:     str,
    s3_prefix:     str,
    dry_run:       bool,
    retry_failed:  bool,
    log_file:      str,
) -> None:

    logger = setup_logging(log_file)
    prefix = "[DRY RUN] " if dry_run else ""
    logger.info(f"{prefix}Executing plan : {plan_file}")
    logger.info(f"{prefix}S3 destination : s3://{s3_bucket}/{s3_prefix}/")
    logger.info(f"Log file       : {log_file}")

    plan = load_plan(plan_file)

    # Optionally reset failed items so they are retried this run
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

    s3_client = boto3.client("s3")

    course_items = plan.get("course_shared_folders", {}).get("to_backup", [])
    user_items   = plan.get("user_home_directories", {}).get("to_backup", [])

    # ---- Course shared folders ----
    logger.info(f"\n--- Course Shared Folders ({len(course_items)} item(s) in plan) ---")
    for item in course_items:
        process_item(
            item, "course_shared_folders",
            s3_client, s3_bucket, s3_prefix,
            plan, plan_file, dry_run, logger,
        )

    # ---- User home directories ----
    logger.info(f"\n--- User Home Directories ({len(user_items)} item(s) in plan) ---")
    for item in user_items:
        process_item(
            item, "home",
            s3_client, s3_bucket, s3_prefix,
            plan, plan_file, dry_run, logger,
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

    # Stamp the plan with the execution time
    if not dry_run:
        plan["metadata"]["last_executed_at"] = datetime.now().isoformat(timespec="seconds")
        save_plan(plan, plan_file, logger)
        logger.info(f"\nPlan file updated: {plan_file}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Execute a backup/deletion plan: upload pending items to S3, "
            "then delete the local copies."
        )
    )
    parser.add_argument(
        "plan_file",
        help="YAML plan file produced by generate_plan.py",
    )
    parser.add_argument(
        "s3_bucket",
        help="Destination S3 bucket name",
    )
    parser.add_argument(
        "--s3-prefix",
        default=None,
        help=(
            "S3 key prefix for all uploads "
            "(default: backups/<YYYYMMDD from plan's generated_at>)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without uploading or deleting anything",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reset previously failed items to pending so they are retried",
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log file path (default: backup_execution_YYYYMMDD_HHMMSS.log)",
    )

    args = parser.parse_args()

    # Derive a default S3 prefix from the plan's generation date so that
    # re-running the executor for the same plan lands in the same S3 location.
    s3_prefix = args.s3_prefix
    if s3_prefix is None:
        try:
            meta         = load_plan(args.plan_file).get("metadata", {})
            generated_at = meta.get("generated_at", datetime.now().isoformat())
            date_slug    = generated_at[:10].replace("-", "")   # e.g. "20240115"
            s3_prefix    = f"backups/{date_slug}"
        except Exception:
            s3_prefix = f"backups/{datetime.now().strftime('%Y%m%d')}"

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
    )


if __name__ == "__main__":
    main()
