#!/usr/bin/env python3
"""
Inspect a backup plan YAML file produced by generate_plan.py:

  - Validates the plan file structure and item fields
  - Extracts unique Canvas course IDs from:
      * Course shared folder names  (e.g. '123456outer' -> course ID 123456)
      * User group memberships      (e.g. 'canvas123456-789012' -> course ID 123456)
  - Reports anomalies (unrecognised folder names, users with no course groups)
  - Generates an SQL query to verify the extracted course IDs against the
    Canvas data database before proceeding with backup and deletion

Canvas group name rules applied during extraction:
  - 'canvas<id>-<group_id>'         yields course ID <id>
  - 'canvas<id>-staff-<group_id>'   ignored (staff group, redundant)
  - 'ondemand-users'                ignored (general access group)
  - anything else                   ignored silently

Usage
-----
    python validate_plan.py backup_plan_20240115_103000.yml
    python validate_plan.py backup_plan_20240115_103000.yml --output verify_courses.sql
"""

import re
import sys
import yaml
import argparse
from collections import defaultdict
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Folder name: one or more digits immediately followed by the literal 'outer'.
# Anything after 'outer' is ignored so names like '123456outer_shared' still match.
# e.g. '123456outer' -> course ID 123456
FOLDER_PATTERN = re.compile(r"^(\d+)outer", re.IGNORECASE)

# User group carrying a Canvas course ID.
# Matches 'canvas<digits>-<digits>' only.
# Staff groups ('canvas<digits>-staff-<digits>') do not match because
# 'staff-<digits>' is not all digits, so the pattern naturally excludes them.
# 'ondemand-users' does not match because it doesn't start with 'canvas<digits>-'.
GROUP_PATTERN = re.compile(r"^canvas(\d+)-\d+$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_course_id(raw: str) -> str:
    """
    Strip leading zeros from a raw digit string to produce a canonical integer
    course ID string. Canvas stores course IDs as integers, so '000123' and
    '123' refer to the same course.
    """
    return str(int(raw))


# ---------------------------------------------------------------------------
# Structure validation
# ---------------------------------------------------------------------------

REQUIRED_TOP_LEVEL   = {"metadata", "course_shared_folders", "user_home_directories"}
REQUIRED_FOLDER_KEYS = {"folder_name", "path", "status"}
REQUIRED_USER_KEYS   = {"username", "path", "status"}


def validate_structure(plan: dict) -> list[str]:
    """
    Check that the plan has the expected top-level sections and that each item
    in both to_backup lists has the minimum required fields.

    Returns a list of issue strings. An empty list means no issues were found.
    """
    issues: list[str] = []

    missing_top = REQUIRED_TOP_LEVEL - set(plan.keys())
    if missing_top:
        issues.append(
            f"Missing top-level section(s): {', '.join(sorted(missing_top))}"
        )
        # Can't safely validate items without the expected structure
        return issues

    for idx, item in enumerate(
        plan.get("course_shared_folders", {}).get("to_backup", [])
    ):
        missing = REQUIRED_FOLDER_KEYS - set(item.keys())
        if missing:
            label = item.get("folder_name", f"item[{idx}]")
            issues.append(
                f"Course folder '{label}' is missing field(s): "
                f"{', '.join(sorted(missing))}"
            )

    for idx, item in enumerate(
        plan.get("user_home_directories", {}).get("to_backup", [])
    ):
        missing = REQUIRED_USER_KEYS - set(item.keys())
        if missing:
            label = item.get("username", f"item[{idx}]")
            issues.append(
                f"User item '{label}' is missing field(s): "
                f"{', '.join(sorted(missing))}"
            )

    return issues


# ---------------------------------------------------------------------------
# Course ID extraction
# ---------------------------------------------------------------------------

def extract_folder_course_ids(
    items: list[dict],
) -> tuple[dict[str, list[str]], list[str]]:
    """
    Extract Canvas course IDs from course shared folder names.

    Returns:
        matched:   { course_id: [folder_name, ...] }
        unmatched: [folder_name, ...]  -- names that did not match the pattern
    """
    matched:   dict[str, list[str]] = defaultdict(list)
    unmatched: list[str]            = []

    for item in items:
        folder_name = item.get("folder_name", "")
        m = FOLDER_PATTERN.match(folder_name)
        if m:
            matched[normalize_course_id(m.group(1))].append(folder_name)
        else:
            unmatched.append(folder_name)

    return dict(matched), unmatched


def extract_group_course_ids(
    items: list[dict],
) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    """
    Extract Canvas course IDs from user group memberships.

    Groups not matching the canvas course pattern (ondemand-users, staff groups,
    etc.) are silently skipped — they are expected and not anomalies.

    Returns:
        matched:               { course_id: [(username, group_name), ...] }
        no_canvas_group_users: [username, ...]
            Users whose group list contained no canvas course group at all.
            These may be orphaned or service accounts worth reviewing.
    """
    matched:               dict[str, list[tuple[str, str]]] = defaultdict(list)
    no_canvas_group_users: list[str]                        = []

    for item in items:
        username  = item.get("username", item.get("path", "unknown"))
        groups    = item.get("groups", [])
        found_any = False

        for group in groups:
            m = GROUP_PATTERN.match(group)
            if m:
                course_id = normalize_course_id(m.group(1))
                matched[course_id].append((username, group))
                found_any = True

        if not found_any:
            no_canvas_group_users.append(username)

    return dict(matched), no_canvas_group_users


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

SQL_TEMPLATE = """\
-- Canvas course verification query
-- Generated  : {generated_at}
-- Plan file  : {plan_file}
-- Course IDs : {course_count}
--
-- Review the results below to confirm these are the correct courses before
-- proceeding with backup and deletion.

SELECT
    c.id                  AS course_id,
    c.name                AS course_name,
    c.enrollment_term_id,
    et.name               AS term_name
FROM courses c
JOIN enrollment_terms et
    ON c.enrollment_term_id = et.id
WHERE c.id IN (
    {id_list}
)
ORDER BY
    et.name,
    c.name;
"""


def generate_sql(course_ids: list[str], plan_file: str) -> str:
    """
    Generate an SQL SELECT query joining courses to enrollment_terms for all
    provided Canvas course IDs.
    """
    id_list = ",\n    ".join(sorted(course_ids, key=int))

    return SQL_TEMPLATE.format(
        generated_at = datetime.now().isoformat(timespec="seconds"),
        plan_file    = plan_file,
        course_count = len(course_ids),
        id_list      = id_list,
    )


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(
    plan_file:             str,
    plan:                  dict,
    structure_issues:      list[str],
    folder_matched:        dict[str, list[str]],
    folder_unmatched:      list[str],
    group_matched:         dict[str, list[tuple[str, str]]],
    no_canvas_group_users: list[str],
    all_course_ids:        list[str],
) -> None:
    """Print a human-readable validation report to stdout."""

    hr1 = "=" * 64
    hr2 = "-" * 64

    print(f"\n{hr1}")
    print(f"  Plan Validation Report")
    print(f"  {plan_file}")
    generated_at = plan.get("metadata", {}).get("generated_at", "unknown")
    print(f"  Plan generated at : {generated_at}")
    print(f"{hr1}")

    # ---- Structure ----
    print(f"\nStructure")
    print(hr2)
    if structure_issues:
        print(f"  {len(structure_issues)} issue(s) found:")
        for issue in structure_issues:
            print(f"  ✗  {issue}")
    else:
        print(f"  ✓  Plan structure is valid")

    # ---- Status summary ----
    course_items = plan.get("course_shared_folders", {}).get("to_backup", [])
    user_items   = plan.get("user_home_directories", {}).get("to_backup", [])

    def tally(items: list[dict], status: str) -> int:
        return sum(1 for i in items if i.get("status") == status)

    print(f"\nItem status summary")
    print(hr2)
    for section_label, items in (
        ("Course folders  ", course_items),
        ("User directories", user_items),
    ):
        counts = "  ".join(
            f"{s}={tally(items, s)}"
            for s in ("pending", "backed_up", "completed", "failed", "skipped")
        )
        print(f"  {section_label}: {counts}")

    # ---- Course shared folders ----
    print(f"\nCourse shared folders  ({len(course_items)} item(s) in to_backup)")
    print(hr2)
    print(f"  Folders with a recognised course ID : {len(folder_matched)}")
    print(f"  Folders with no recognised course ID: {len(folder_unmatched)}")

    if folder_unmatched:
        print(
            f"\n  WARNING — the following folder names did not match the expected\n"
            f"  '<digits>outer' pattern and no course ID could be extracted:"
        )
        for name in sorted(folder_unmatched):
            print(f"    ✗  {name}")

    # ---- User home directories ----
    unique_users_with_group = {
        username
        for users in group_matched.values()
        for username, _ in users
    }

    print(f"\nUser home directories  ({len(user_items)} item(s) in to_backup)")
    print(hr2)
    print(f"  Users with at least one canvas course group : {len(unique_users_with_group)}")
    print(f"  Users with no canvas course group           : {len(no_canvas_group_users)}")

    if no_canvas_group_users:
        print(
            f"\n  NOTE — the following users have no canvas course group. They may\n"
            f"  be orphaned or service accounts. Verify before deleting:"
        )
        for username in sorted(no_canvas_group_users):
            print(f"    -  {username}")

    # ---- Canvas course IDs ----
    only_folders = set(folder_matched) - set(group_matched)
    only_groups  = set(group_matched)  - set(folder_matched)
    in_both      = set(folder_matched) & set(group_matched)

    print(f"\nCanvas course IDs")
    print(hr2)
    print(f"  Total unique course IDs  : {len(all_course_ids)}")
    print(f"  From folder names only   : {len(only_folders)}")
    print(f"  From user groups only    : {len(only_groups)}")
    print(f"  From both                : {len(in_both)}")

    if only_folders:
        print(
            f"\n  NOTE — course IDs found in folder names but not in any user group\n"
            f"  (no users enrolled in these courses were flagged for removal):"
        )
        for cid in sorted(only_folders, key=int):
            print(f"    {cid}  (folders: {', '.join(folder_matched[cid])})")

    if only_groups:
        print(
            f"\n  NOTE — course IDs found in user groups but not in any folder name\n"
            f"  (no shared folder for these courses was flagged for removal):"
        )
        for cid in sorted(only_groups, key=int):
            users = sorted({u for u, _ in group_matched[cid]})
            user_str = ", ".join(users[:5])
            if len(users) > 5:
                user_str += f"  (+{len(users) - 5} more)"
            print(f"    {cid}  (users: {user_str})")

    if all_course_ids:
        print(f"\n  Full list of course IDs to be processed:")
        for cid in all_course_ids:
            parts = []
            if cid in folder_matched:
                parts.append(f"{len(folder_matched[cid])} folder(s)")
            if cid in group_matched:
                user_count = len({u for u, _ in group_matched[cid]})
                parts.append(f"{user_count} user(s)")
            print(f"    {cid}  [{', '.join(parts)}]")

    print(f"\n{hr1}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def validate_plan(plan_file: str, output_file: "str | None") -> None:
    try:
        with open(plan_file, "r") as fh:
            plan = yaml.safe_load(fh)
    except FileNotFoundError:
        print(f"Error: plan file not found: {plan_file}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as exc:
        print(f"Error: could not parse plan file:\n  {exc}", file=sys.stderr)
        sys.exit(1)

    structure_issues = validate_structure(plan)

    course_items = plan.get("course_shared_folders", {}).get("to_backup", [])
    user_items   = plan.get("user_home_directories", {}).get("to_backup", [])

    folder_matched, folder_unmatched       = extract_folder_course_ids(course_items)
    group_matched,  no_canvas_group_users  = extract_group_course_ids(user_items)

    all_course_ids = sorted(
        set(folder_matched) | set(group_matched),
        key=int,
    )

    print_report(
        plan_file,
        plan,
        structure_issues,
        folder_matched,
        folder_unmatched,
        group_matched,
        no_canvas_group_users,
        all_course_ids,
    )

    if not all_course_ids:
        print("No Canvas course IDs found in this plan. No SQL query generated.")
        if structure_issues:
            sys.exit(1)
        return

    sql = generate_sql(all_course_ids, plan_file)

    if output_file:
        Path(output_file).write_text(sql)
        print(f"SQL query written to: {output_file}\n")
    else:
        print("--- SQL Query ---\n")
        print(sql)

    if structure_issues:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a backup plan YAML file and generate an SQL query to "
            "verify the Canvas course IDs against the Canvas data database "
            "before proceeding with backup and deletion."
        )
    )
    parser.add_argument(
        "plan_file",
        help="YAML plan file produced by generate_plan.py",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Write the SQL query to this file instead of printing to stdout",
    )

    args = parser.parse_args()
    validate_plan(args.plan_file, args.output)


if __name__ == "__main__":
    main()
