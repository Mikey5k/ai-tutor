#!/usr/bin/env python3
"""
ingest_course.py — Ingest a YouTube course into the AI Tutor lesson manager.

Usage:
    python tools/ingest_course.py <youtube-url> [--title "Course Title"] [--student-id default]

Examples:
    python tools/ingest_course.py https://www.youtube.com/watch?v=abcd1234
    python tools/ingest_course.py https://youtu.be/abcd1234 --title "Python Basics"
    python tools/ingest_course.py https://youtu.be/abcd1234 --title "Python Basics" --student-id alice
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

LESSON_MANAGER_URL = "http://127.0.0.1:9105"
POLL_INTERVAL_SECONDS = 2


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest a YouTube course into the AI Tutor lesson manager."
    )
    parser.add_argument(
        "url",
        help="YouTube video or playlist URL to ingest.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Human-readable course title (optional; server will derive one if omitted).",
    )
    parser.add_argument(
        "--student-id",
        default="default",
        dest="student_id",
        help="Student ID to associate the course with (default: 'default').",
    )
    return parser


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


def get_json(url: str, timeout: int = 15) -> dict:
    """GET url and return parsed JSON body."""
    req = urllib.request.Request(url, method="GET")
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


def ingest_course(url: str, title: str | None, student_id: str) -> str:
    """
    Send the ingest_course tool call to the lesson-manager.
    Returns the course_id from the server response.
    """
    args: dict = {"url": url}
    if title:
        args["course_title"] = title
    args["student_id"] = student_id

    payload = {"tool": "ingest_course", "args": args}

    print(f"Sending ingest request to lesson-manager...")
    print(f"  URL:        {url}")
    if title:
        print(f"  Title:      {title}")
    print(f"  Student ID: {student_id}")
    print()

    response = post_json(f"{LESSON_MANAGER_URL}", payload)

    course_id = response.get("course_id")
    if not course_id:
        print(
            f"ERROR: lesson-manager did not return a course_id.\n"
            f"       Response: {json.dumps(response, indent=2)}",
            file=sys.stderr,
        )
        sys.exit(1)

    return course_id


def poll_ingestion_status(course_id: str) -> dict:
    """
    Poll GET /ingestion_status/<course_id> every POLL_INTERVAL_SECONDS seconds.
    Prints progress updates. Returns the final status response when done.
    """
    status_url = f"{LESSON_MANAGER_URL}/ingestion_status/{course_id}"
    last_message = ""
    dots = 0

    print(f"Polling ingestion status for course_id: {course_id}")
    print()

    while True:
        status_data = get_json(status_url)

        state: str = status_data.get("state", "unknown").lower()
        progress: int | float = status_data.get("progress", 0)
        message: str = status_data.get("message", "")

        # Only reprint if something changed
        if message != last_message:
            dots = 0
            last_message = message
            bar_filled = int(progress / 5)  # 20-char bar for 0-100%
            bar = "#" * bar_filled + "-" * (20 - bar_filled)
            print(f"  [{bar}] {int(progress):3d}%  {message}")

        if state in ("complete", "done", "finished", "success"):
            print()
            return status_data

        if state in ("error", "failed", "failure"):
            error_detail = status_data.get("error", "No error detail provided.")
            print(f"\nERROR: Ingestion failed — {error_detail}", file=sys.stderr)
            sys.exit(1)

        dots += 1
        time.sleep(POLL_INTERVAL_SECONDS)


def print_summary(course_id: str, status_data: dict) -> None:
    """Print the final course_id and curriculum summary."""
    print("=" * 60)
    print("  Ingestion complete!")
    print("=" * 60)
    print()
    print(f"  Course ID : {course_id}")

    derived_title = status_data.get("course_title") or status_data.get("title")
    if derived_title:
        print(f"  Title     : {derived_title}")

    module_count = status_data.get("module_count") or status_data.get("modules")
    if module_count is not None:
        print(f"  Modules   : {module_count}")

    summary = status_data.get("curriculum_summary") or status_data.get("summary")
    if summary:
        print()
        print("  Curriculum summary:")
        print("-" * 60)
        # Wrap/indent the summary
        for line in summary.strip().splitlines():
            print(f"    {line}")
        print("-" * 60)

    # Print full response for any extra fields not captured above
    extra_keys = {
        k: v
        for k, v in status_data.items()
        if k not in {
            "state", "progress", "message", "course_id", "course_title",
            "title", "module_count", "modules", "curriculum_summary", "summary",
        }
    }
    if extra_keys:
        print()
        print("  Additional info:")
        for k, v in extra_keys.items():
            print(f"    {k}: {v}")

    print()


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    course_id = ingest_course(args.url, args.title, args.student_id)
    status_data = poll_ingestion_status(course_id)
    print_summary(course_id, status_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
