#!/usr/bin/env python3
"""
Generate a YAML plan identifying course shared folders and user home directories
that are not associated with any active course, marking them for backup and removal.

Usage:
    python generate_plan.py active_courses.txt
    python generate_plan.py active_courses.txt --output my_plan.yml
    python generate_plan.py active_courses.txt --course-shared-path /data/courses --home-path /data/home
"""

import os
import subprocess
import pwd
import yaml
import argparse
from datetime import datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_active_courses(courses_file: str) -> list[str]:
    """
    Load active course IDs from a plain-text file.
    One course ID per line; lines beginning with '#' and blank lines are ignored.
    """
    courses = []
    with open(courses_file, "r") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                courses.append(line)
    return courses


def folder_matches_active_course(
    folder_name: str, active_courses: list[str]
) -> tuple[bool, str | None]:
    """
    Return (True, matched_course_id) if the folder name contains any active
    course ID (case-insensitive substring match), otherwise (False, None).
    """
    folder_upper = folder_name.upper()
    for course_id in active_courses:
        if course_id.upper() in folder_upper:
            return True, course_id
    return False, None


def get_user_groups(username: str) -> list[str]:
    """
    Return a sorted list of all group names the user belongs to.

    Uses 'id -Gn' rather than grp.getgrall() because getgrall() relies on
    getgrent() enumeration, which SSSD disables by default. The 'id' command
    goes through the initgroups NSS path that SSSD always supports.
    """
    try:
        result = subprocess.run(
            ["id", "-Gn", username],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return sorted(result.stdout.strip().split())

        # returncode 1 typically means the user wasn't found
        return []

    except subprocess.TimeoutExpired:
        print(f"  Warning: 'id -Gn {username}' timed out — SSSD may be unreachable")
        return []
    except OSError as exc:
        print(f"  Warning: could not retrieve groups for '{username}': {exc}")
        return []


def user_in_active_course(
    groups: list[str], active_courses: list[str]
) -> tuple[bool, str | None, str | None]:
    """
    Return (True, matched_group, matched_course_id) if any of the user's
    group names contains an active course ID (case-insensitive substring
    match), otherwise (False, None, None).
    """
    for group in groups:
        for course_id in active_courses:
            if course_id.upper() in group.upper():
                return True, group, course_id
    return False, None, None


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

def scan_course_shared_folders(
    base_path: str, active_courses: list[str]
) -> tuple[list[dict], list[dict]]:
    """
    Walk base_path and split every sub-directory into to_backup or to_keep.
    Returns (to_backup, to_keep).
    """
    to_backup: list[dict] = []
    to_keep:   list[dict] = []

    if not os.path.isdir(base_path):
        print(f"  Warning: path '{base_path}' not found or is not a directory.")
        return to_backup, to_keep

    for entry in sorted(os.scandir(base_path), key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False):
            continue

        is_active, matched_course = folder_matches_active_course(
            entry.name, active_courses
        )

        if is_active:
            to_keep.append({
                "folder_name":       entry.name,
                "path":              entry.path,
                "matched_course_id": matched_course,
                "reason":            f"Folder name contains active course ID '{matched_course}'",
            })
        else:
            to_backup.append({
                "folder_name": entry.name,
                "path":        entry.path,
                "reason":      "Folder name does not contain any active course ID",
                "status":      "pending",
            })

    return to_backup, to_keep


def scan_user_home_directories(
    base_path: str, active_courses: list[str]
) -> tuple[list[dict], list[dict]]:
    """
    Walk base_path and split every user directory into to_backup or to_keep
    based on whether the user's groups match any active course.
    Returns (to_backup, to_keep).
    """
    to_backup: list[dict] = []
    to_keep:   list[dict] = []

    if not os.path.isdir(base_path):
        print(f"  Warning: path '{base_path}' not found or is not a directory.")
        return to_backup, to_keep

    for entry in sorted(os.scandir(base_path), key=lambda e: e.name):
        if not entry.is_dir(follow_symlinks=False):
            continue

        username = entry.name

        if username == 'root':
            to_keep.append({
                "username":          username,
                "path":              entry.path,
                "groups":            "root",
                "matched_group":     "root",
                "matched_course_id": "N/A",
                "reason":            "root user is always kept"
            })
            continue

        groups = get_user_groups(username)
        is_active, matched_group, matched_course = user_in_active_course(
            groups, active_courses
        )

        if is_active:
            to_keep.append({
                "username":          username,
                "path":              entry.path,
                "groups":            groups,
                "matched_group":     matched_group,
                "matched_course_id": matched_course,
                "reason": (
                    f"User belongs to group '{matched_group}' "
                    f"which matches active course '{matched_course}'"
                ),
            })
        else:
            item: dict = {
                "username": username,
                "path":     entry.path,
                "groups":   groups,
                "reason":   "No group membership matches an active course",
                "status":   "pending",
            }
            # Flag orphaned home directories whose owner no longer exists in passwd
            try:
                pwd.getpwnam(username)
            except KeyError:
                item["note"] = "User account not found"
            to_backup.append(item)

    return to_backup, to_keep


# ---------------------------------------------------------------------------
# Plan assembly
# ---------------------------------------------------------------------------

def generate_plan(
    active_courses_file: str,
    output_file: str,
    course_shared_path: str = "/shared/courseSharedFolders",
    home_path:          str = "/shared/home",
) -> None:

    print(f"Loading active courses from: {active_courses_file}")
    active_courses = load_active_courses(active_courses_file)
    print(f"  {len(active_courses)} active course(s): {', '.join(active_courses)}")

    print(f"\nScanning course shared folders: {course_shared_path}")
    course_to_backup, course_to_keep = scan_course_shared_folders(
        course_shared_path, active_courses
    )
    print(f"  To backup : {len(course_to_backup)}")
    print(f"  To keep   : {len(course_to_keep)}")

    print(f"\nScanning user home directories: {home_path}")
    users_to_backup, users_to_keep = scan_user_home_directories(
        home_path, active_courses
    )
    print(f"  To backup : {len(users_to_backup)}")
    print(f"  To keep   : {len(users_to_keep)}")

    plan = {
        "metadata": {
            "generated_at":        datetime.now().isoformat(timespec="seconds"),
            "active_courses_file": os.path.abspath(active_courses_file),
            "active_courses":      active_courses,
            "paths": {
                "course_shared_folders": course_shared_path,
                "home_directories":      home_path,
            },
            "summary": {
                "course_folders_to_backup": len(course_to_backup),
                "course_folders_to_keep":   len(course_to_keep),
                "users_to_backup":          len(users_to_backup),
                "users_to_keep":            len(users_to_keep),
            },
        },
        "course_shared_folders": {
            "to_backup": course_to_backup,
            "to_keep":   course_to_keep,
        },
        "user_home_directories": {
            "to_backup": users_to_backup,
            "to_keep":   users_to_keep,
        },
    }

    with open(output_file, "w") as fh:
        yaml.dump(plan, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"\nPlan written to: {output_file}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a YAML backup/deletion plan for inactive course folders "
            "and user home directories."
        )
    )
    parser.add_argument(
        "active_courses_file",
        help="Text file listing active course IDs, one per line.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output YAML file (default: backup_plan_YYYYMMDD_HHMMSS.yml)",
    )
    parser.add_argument(
        "--course-shared-path",
        default="/shared/courseSharedFolders",
        help="Root of course shared folders (default: /shared/courseSharedFolders)",
    )
    parser.add_argument(
        "--home-path",
        default="/shared/home",
        help="Root of user home directories (default: /shared/home)",
    )

    args = parser.parse_args()

    output_file = (
        args.output
        or f"backup_plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.yml"
    )

    generate_plan(
        active_courses_file=args.active_courses_file,
        output_file=output_file,
        course_shared_path=args.course_shared_path,
        home_path=args.home_path,
    )


if __name__ == "__main__":
    main()
