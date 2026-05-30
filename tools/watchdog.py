#!/usr/bin/env python3
"""
MCP Watchdog — starts and restarts HTTP MCP servers if they die.

Usage:
    python E:/ai-tutor/tools/watchdog.py

Starts all configured HTTP MCP servers, monitors them, and restarts
any that exit unexpectedly. Run this once before starting Claude Code.
"""
import subprocess
import sys
import time
import urllib.request
import urllib.error
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("watchdog")

PYTHON = sys.executable

# ---------------------------------------------------------------------------
# Server definitions
# ---------------------------------------------------------------------------

@dataclass
class ManagedServer:
    name: str
    script: str
    health_url: str           # GET this URL — 200 = healthy
    restart_delay: float = 5  # seconds to wait before restarting
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)
    restarts: int = 0


SERVERS = [
    ManagedServer(
        name="voice",
        script="E:/ai-tutor/mcp-servers/voice/server.py",
        health_url="http://localhost:9103/health",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start(server: ManagedServer) -> None:
    logger.info(f"Starting {server.name}...")
    server.proc = subprocess.Popen(
        [PYTHON, server.script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logger.info(f"{server.name} started (pid={server.proc.pid})")


def ensure_running(server: ManagedServer) -> None:
    """Start or restart the server if it is not running/healthy."""
    if server.proc is None:
        start(server)
        return

    # Check if process exited
    if server.proc.poll() is not None:
        rc = server.proc.returncode
        server.restarts += 1
        logger.warning(
            f"{server.name} exited (rc={rc}), restart #{server.restarts} "
            f"in {server.restart_delay}s..."
        )
        time.sleep(server.restart_delay)
        start(server)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    logger.info("MCP Watchdog starting...")

    # Initial start
    for s in SERVERS:
        start(s)

    # Give servers time to boot
    logger.info("Waiting 15s for servers to initialise...")
    time.sleep(15)

    logger.info("Watchdog running. Ctrl+C to stop.")
    try:
        while True:
            for s in SERVERS:
                ensure_running(s)
                if not is_healthy(s.health_url):
                    logger.debug(f"{s.name} health check failed (may still be loading)")

            time.sleep(10)  # check every 10 seconds
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        for s in SERVERS:
            if s.proc and s.proc.poll() is None:
                s.proc.terminate()
                logger.info(f"Stopped {s.name}")


if __name__ == "__main__":
    main()
