# Course Data Backup and Removal

This document covers the process for backing up and removing inactive course
shared folders and user home directories. Backups are stored in S3 under a
structured prefix so that backed-up data can be located and restored if needed.

## Overview

The backup process uses two scripts:

| Script | Purpose |
|---|---|
| `generate_plan.py` | Scans course shared folders and user home directories, compares them against a list of active course IDs, and produces a YAML plan file listing what will be backed up and removed |
| `execute_plan.py` | Reads the plan file, uploads each flagged directory to S3, deletes the local copy, and records the outcome back into the plan file |

The plan file is your checkpoint and audit record. It is updated after every
item is processed and synced to S3 continuously, so if a run is interrupted
it can be resumed from a different machine without repeating work that has
already been completed.

---

## Prerequisites

- `uv` is preferred as a Python package management solution. This directory
  includes a `pyproject.toml` file with the required dependencies, also outlined
  below in case they need to be reproduced.
    - Python 3.10 or later
    - The following Python packages (install via `pip install boto3 pyyaml`):
        - `boto3`
        - `pyyaml`
- Sign in to the appropriate AWS account through the [AWS SAML CLI](https://github.huit.harvard.edu/HUIT/aws-login-saml-cli)
- `sudo` permissions on the HUIT OOD system, which should include:
    - Read access to `/shared/courseSharedFolders` and `/shared/home`
    - Sufficient permission to delete directories under those paths

---

## Running a Backup

### Step 1: Prepare the active courses list

Create a plain text file listing every **currently active** course ID, one per
line. Any course not in this list will be flagged for backup and removal. A good
source for this information is the [Courses Using Compute Environments - summary
for reporting up and
out](https://docs.google.com/spreadsheets/d/1YwDgG4S768SQhtP3t-eBB3MfxCHOBkZqm4V6ToP-2pk/edit?gid=0#gid=0)
Google Sheet, which should already have the course IDs in a column ready to copy
out of the spreadsheet.

```text
# active_courses.txt
# Lines beginning with # and blank lines are ignored.
CS101-2024SP
BIO220-2024SP
ENG315-2024SP
```

A course ID is matched against folder names and user group names as a
case-insensitive substring, so `CS101-2024SP` will match a folder named
`CS101-2024SP_shared` or a group named `students-cs101-2024sp`.

### Step 2: Generate a plan

```bash
python generate_plan.py active_courses.txt
```

This produces a timestamped YAML plan file in the current directory, for
example `backup_plan_20240115_103000.yml`. It does not modify, move, or delete
anything.

Optional arguments:

```bash
# Specify an output filename
python generate_plan.py active_courses.txt --output my_plan.yml

# Override the default scan paths
python generate_plan.py active_courses.txt \
    --course-shared-path /shared/courseSharedFolders \
    --home-path /shared/home
```

### Step 3: Review the plan

Open the generated YAML file and check both to_backup sections before
proceeding. Each section also includes a to_keep list showing which folders
and users were matched to an active course and why, so you can verify the
matching logic behaved as expected.

Key fields to check in the to_backup lists:

| Field | Meaning |
| --- | --- |
| path | The local path that will be uploaded and deleted |
| reason | Why this item was flagged |
| groups | (User items only) The user's current group memberships |
| note | Present if the user account no longer exists in the directory |

If anything looks wrong, correct the `active_courses.txt` file and re-run
`generate_plan.py` to produce a fresh plan before continuing.

### Step 4: Dry run

Run the executor in dry-run mode to confirm what will be uploaded and deleted
without making any changes. Nothing is written to S3 and no local files are
touched.

```bash
python execute_plan.py backup_plan_20240115_103000.yml S3_BUCKET_PLACEHOLDER --dry-run
```

### Step 5: Execute the plan

When you are satisfied with the plan, run the executor for real:

```bash
python execute_plan.py backup_plan_20240115_103000.yml S3_BUCKET_PLACEHOLDER
```

For each item the executor will:

1. Upload the full directory tree to S3
2. Delete the local copy only after the upload succeeds
3. Record the outcome (`completed`, `failed`, or `skipped`) in the plan file
4. Sync the updated plan file and the execution log to S3

Progress is printed to the terminal and written to a timestamped log file in
the current directory, for example `backup_execution_20240115_110000.log`.

If an item's upload fails, the local copy is not deleted and the item is
marked `failed` in the plan. The run continues with the remaining items. See
[Resuming an Interrupted Run](TODO: figure out GH internal links) for how to retry
failed items.

## Resuming an Interrupted Run

If a run is interrupted (network loss, machine restart, etc.), the plan file
in S3 reflects everything that was completed before the interruption. Any
administrator can resume the run from any machine that has network access to
`S3_BUCKET_PLACEHOLDER`.

On the resuming machine:

```bash
# Download the latest plan from S3 and continue processing pending items.
# The plan filename and --s3-prefix must match the original run.
python execute_plan.py backup_plan_20240115_103000.yml S3_BUCKET_PLACEHOLDER \
    --from-s3 \
    --s3-prefix backups/20240115
```

`--from-s3` overwrites any local copy of the plan file with the S3 version,
which is the authoritative record of what has already been done. Items already
marked `completed` are skipped automatically.

To also retry any items that were marked `failed` during a previous run:

```bash
python execute_plan.py backup_plan_20240115_103000.yml S3_BUCKET_PLACEHOLDER \
    --from-s3 \
    --s3-prefix backups/20240115 \
    --retry-failed
```

> Note on --s3-prefix: The prefix defaults to backups/YYYYMMDD derived
> from the date in the plan filename. If the plan filename follows the standard
> naming convention (backup_plan_YYYYMMDD_HHMMSS.yml) you do not need to
> pass --s3-prefix explicitly — it will be derived automatically. Pass it
> explicitly only if you used a custom prefix during the original run.

## Finding Backed-Up Data in S3

### S3 layout

All content for a given backup run is stored under a single date-based prefix:

```bash
s3://S3_BUCKET_PLACEHOLDER/
└── backups/
    └── YYYYMMDD/                          ← date the plan was generated
        ├── plan/
        │   └── backup_plan_YYYYMMDD_HHMMSS.yml
        ├── logs/
        │   ├── backup_execution_YYYYMMDD_HHMMSS.log   ← first run
        │   └── backup_execution_YYYYMMDD_HHMMSS.log   ← any resumed runs
        ├── course_shared_folders/
        │   ├── CS099-2023FA_shared/
        │   │   └── ... (original folder contents)
        │   └── PHY110-2023FA/
        │       └── ...
        └── home/
            ├── jsmith/
            │   └── ... (original home directory contents)
            └── bjones/
                └── ...
```

### Browsing via AWS CLI

List all backup runs:

```bash
aws s3 ls s3://S3_BUCKET_PLACEHOLDER/backups/
```

List the contents of a specific run:

```bash
aws s3 ls s3://S3_BUCKET_PLACEHOLDER/backups/20240115/ --recursive
```

List only backed-up course folders from a run:

```bash
aws s3 ls s3://S3_BUCKET_PLACEHOLDER/backups/20240115/course_shared_folders/
```

List only backed-up user home directories from a run:

```bash
aws s3 ls s3://S3_BUCKET_PLACEHOLDER/backups/20240115/home/
```

Check whether a specific user or folder was backed up:

```bash
aws s3 ls s3://S3_BUCKET_PLACEHOLDER/backups/ --recursive | grep jsmith
```

### Browsing via AWS Console

1. Open the S3 console
2. Navigate to S3_BUCKET_PLACEHOLDER
3. Open the backups/ folder and select the date of the relevant run
4. The plan/ folder contains the YAML record of what was backed up
5. The course_shared_folders/ and home/ folders contain the backed-up data

## Downloading Backed-Up Data

To restore a single user's home directory:

```bash
aws s3 cp s3://S3_BUCKET_PLACEHOLDER/backups/20240115/home/jsmith/ \
    /shared/home/jsmith/ \
    --recursive
```

To restore a course shared folder:

```bash
aws s3 cp s3://S3_BUCKET_PLACEHOLDER/backups/20240115/course_shared_folders/CS099-2023FA_shared/ \
    /shared/courseSharedFolders/CS099-2023FA_shared/ \
    --recursive
```

To restore everything from a run:

```bash
aws s3 cp s3://S3_BUCKET_PLACEHOLDER/backups/20240115/ /restore/20240115/ --recursive
```

## Reviewing Logs and Plan Records

The YAML plan file is the primary audit record. After a completed run, each
item in the `to_backup` lists will have:

| Field | Meaning |
| --- | --- |
| status | completed, failed, or skipped |
| completed_at | ISO timestamp of when the item finished successfully |
| s3_location | S3 URI where the backup was written |
| failed_at | ISO timestamp of the failure (failed items only) |
| error | Error message (failed items only) |

To download the plan file for a given run:

```bash
aws s3 cp s3://S3_BUCKET_PLACEHOLDER/backups/20240115/plan/backup_plan_20240115_103000.yml .
```

To download all logs for a given run:

```bash
aws s3 cp s3://S3_BUCKET_PLACEHOLDER/backups/20240115/logs/ ./logs/ --recursive
```
