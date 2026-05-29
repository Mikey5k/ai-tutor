#!/usr/bin/env python3
"""
test_mcp_servers.py — Health-check all MCP servers defined in config.json.

Usage:
    python tools/test_mcp_servers.py

Exit codes:
    0 — all servers healthy
    1 — one or more servers failed
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

# Map config key -> display name
SERVER_DISPLAY_NAMES = {
    "overlay": "overlay",
    "desktop_control": "desktop-control",
    "browser_control": "browser-control",
    "vision_fallback": "vision-fallback",
    "voice": "voice",
    "lesson_manager": "lesson-manager",
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"ERROR: config.json not found at {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def check_health(name: str, host: str, port: int, timeout: int = 5) -> tuple[bool, str]:
    """
    Send GET /health to http://<host>:<port>/health.
    Returns (is_healthy, message).
    """
    url = f"http://{host}:{port}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if 200 <= status < 300:
                try:
                    body = resp.read().decode("utf-8", errors="replace")
                except Exception:
                    body = ""
                return True, f"HTTP {status}" + (f" — {body.strip()}" if body.strip() else "")
            else:
                return False, f"HTTP {status} (unexpected status)"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return False, f"Connection error: {exc.reason}"
    except TimeoutError:
        return False, f"Timed out after {timeout}s"
    except Exception as exc:
        return False, f"Unexpected error: {exc}"


def main() -> int:
    config = load_config()
    mcp_servers: dict = config.get("mcp_servers", {})

    if not mcp_servers:
        print("ERROR: No mcp_servers found in config.json", file=sys.stderr)
        return 1

    total = len(mcp_servers)
    healthy_count = 0
    results: list[tuple[str, bool, str]] = []

    col_name = max(len(SERVER_DISPLAY_NAMES.get(k, k)) for k in mcp_servers) + 2

    print()
    print("=" * 60)
    print("  AI Tutor — MCP Server Health Check")
    print("=" * 60)
    print()

    for config_key, server_cfg in mcp_servers.items():
        display_name = SERVER_DISPLAY_NAMES.get(config_key, config_key)
        host: str = server_cfg.get("host", "127.0.0.1")
        port: int = server_cfg.get("port")

        if port is None:
            is_healthy = False
            message = "No port defined in config.json"
        else:
            is_healthy, message = check_health(display_name, host, port)

        if is_healthy:
            healthy_count += 1
            status_label = "OK    "
        else:
            status_label = "FAILED"

        label = f"{display_name} (:{port})" if port else display_name
        results.append((label, is_healthy, message))
        print(f"  [{status_label}]  {label:<{col_name}}  {message}")

    print()
    print("-" * 60)
    print(f"  Summary: {healthy_count}/{total} servers healthy")
    print("-" * 60)
    print()

    if healthy_count < total:
        failed = [r[0] for r in results if not r[1]]
        print("  Failed servers:")
        for name in failed:
            print(f"    - {name}")
        print()
        return 1

    print("  All servers are up and healthy.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
