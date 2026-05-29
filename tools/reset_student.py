#!/usr/bin/env python3
"""
reset_student.py — Reset a student's progress in the AI Tutor lesson manager.

Usage:
    python tools/reset_student.py [--student-id default]

Examples:
    python tools/reset_student.py
    python tools/reset_student.py --student-id alice

WARNING: This action cannot be undone. All progress, test scores, and the
         student model for the specified student will be permanently deleted.
"""

import argparse
import json
import sys
import urllib.request
import urllib.error

LESSON_MANAGER_URL = "http://127.0.0.1:9105"


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset a student's progress in the AI Tutor lesson manager."
    )
    parser.add_argument(
        "--student-id",
        default="default",
        dest="student_id",
        help="ID of the student to reset (default: 'default').",
    )
    return parser


def confirm_reset(student_id: str) -> bool:
    """
    Prompt the user for confirmation. Returns True only if the user types 'y' or 'Y'.
    """
    prompt = f"Reset student '{student_id}'? This cannot be undone. [y/N] "
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.lower() == "y"


def post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    """POST JSON payload to url, return parsed response body."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"ERROR: Server returned HTTP {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(
            f"ERROR: Could not connect to lesson-manager at {LESSON_MANAGER_URL}.\n"
            f"       Is the server running? ({exc.reason})",
            file=sys.stderr,
        )
        sys.exit(1)


def reset_student(student_id: str) -> dict:
    """
    Send the reset_student tool call to the lesson-manager.
    Returns the server response dict.
    """
    payload = {
        "tool": "reset_student",
        "args": {"student_id": student_id},
    }
    return post_json(LESSON_MANAGER_URL, payload)


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    student_id: str = args.student_id

    print()
    print("=" * 60)
    print("  AI Tutor — Reset Student")
    print("=" * 60)
    print()
    print(f"  Student ID : {student_id}")
    print()

    if not confirm_reset(student_id):
        print()
        print("  Reset cancelled. No changes were made.")
        print()
        return 0

    print()
    print(f"  Resetting student '{student_id}'...")

    response = reset_student(student_id)

    success: bool = response.get("success", False)
    message: str = response.get("message", "")

    if success:
        print()
        print("=" * 60)
        print(f"  Student '{student_id}' has been reset successfully.")
        if message:
            print(f"  {message}")
        print("=" * 60)
        print()
        return 0
    else:
        error_detail = response.get("error") or message or "No error detail provided."
        print(f"\nERROR: Reset failed — {error_detail}", file=sys.stderr)
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
