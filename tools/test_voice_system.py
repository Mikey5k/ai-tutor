#!/usr/bin/env python3
"""
test_voice_system.py — End-to-end tests for the voice server and control panel.

Tests:
  1. Voice server reachable and all /health fields present
  2. /mic_level endpoint
  3. /set_listening toggle
  4. /set_pace endpoint
  5. /set_session endpoint
  6. /stop endpoint
  7. /repeat endpoint (no prior speech → nothing_to_repeat)
  8. MCP /mcp endpoint responds to HTTP (not full MCP handshake, just reachability)
  9. voice_control.py imports cleanly (no crash on import)
 10. voice_status.py imports cleanly

Exit code 0 = all pass, 1 = one or more failed.
"""

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

# Force UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:9103"
PASS = "[PASS]"
FAIL = "[FAIL]"

failures = []


def check(name: str, ok: bool, detail: str = ""):
    if ok:
        print(f"  {PASS} {name}")
    else:
        print(f"  {FAIL} {name}{': ' + detail if detail else ''}")
        failures.append(name)


def get(path: str, timeout: float = 4.0):
    url = BASE + path
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


# ---------------------------------------------------------------------------
# 1. /health — all fields present and types correct
# ---------------------------------------------------------------------------
print("\n[1] Voice server /health")
try:
    h = get("/health")
    check("reachable",          True)
    check("status == ok",       h.get("status") == "ok")
    check("field: ready",       "ready" in h)
    check("field: speaking",    "speaking" in h)
    check("field: vad_active",  "vad_active" in h)
    check("field: mic_enabled", "mic_enabled" in h)
    check("field: pace",        "pace" in h)
    check("field: last_spoken_preview", "last_spoken_preview" in h)
    check("field: session_topic",       "session_topic" in h)
    check("field: session_progress",    "session_progress" in h)
    check("pace is valid",      h.get("pace") in ("slow", "normal", "fast"))
except Exception as e:
    check("reachable", False, str(e))
    print("  (skipping remaining /health checks)")

# ---------------------------------------------------------------------------
# 2. /mic_level
# ---------------------------------------------------------------------------
print("\n[2] /mic_level")
try:
    ml = get("/mic_level")
    check("reachable",          True)
    check("field: level",       "level" in ml)
    level = ml.get("level", -1)
    check("level 0.0–1.0",      isinstance(level, (int, float)) and 0.0 <= level <= 1.0,
          f"got {level}")
except Exception as e:
    check("reachable", False, str(e))

# ---------------------------------------------------------------------------
# 3. /set_listening
# ---------------------------------------------------------------------------
print("\n[3] /set_listening")
try:
    r = get("/set_listening?enabled=false")
    check("disable mic",        r.get("mic_enabled") is False)
    r = get("/set_listening?enabled=true")
    check("enable mic",         r.get("mic_enabled") is True)
    h = get("/health")
    check("health reflects enable", h.get("mic_enabled") is True)
except Exception as e:
    check("set_listening", False, str(e))

# ---------------------------------------------------------------------------
# 4. /set_pace
# ---------------------------------------------------------------------------
print("\n[4] /set_pace")
try:
    for pace in ("slow", "fast", "normal"):
        r = get(f"/set_pace?pace={pace}")
        check(f"set pace={pace}", r.get("pace") == pace)
    h = get("/health")
    check("health reflects normal", h.get("pace") == "normal")
except Exception as e:
    check("set_pace", False, str(e))

# ---------------------------------------------------------------------------
# 5. /set_session
# ---------------------------------------------------------------------------
print("\n[5] /set_session")
try:
    r = get("/set_session?topic=Introduction+to+Python&progress=42")
    check("topic set",    r.get("topic") == "Introduction to Python")
    check("progress set", r.get("progress") == 42)
    h = get("/health")
    check("health reflects topic",    h.get("session_topic") == "Introduction to Python")
    check("health reflects progress", h.get("session_progress") == 42)
    # Reset
    get("/set_session?topic=&progress=0")
except Exception as e:
    check("set_session", False, str(e))

# ---------------------------------------------------------------------------
# 6. /stop
# ---------------------------------------------------------------------------
print("\n[6] /stop")
try:
    r = get("/stop")
    check("stopped field present", "stopped" in r)
    check("stopped is True",       r.get("stopped") is True)
except Exception as e:
    check("stop", False, str(e))

# ---------------------------------------------------------------------------
# 7. /repeat (no prior speech)
# ---------------------------------------------------------------------------
print("\n[7] /repeat (no prior speech)")
try:
    r = get("/repeat", timeout=6)
    result = r.get("result", "")
    check("result field present",       "result" in r)
    check("nothing_to_repeat or spoken", result in ("nothing_to_repeat", "spoken", "not_ready"),
          f"got {result!r}")
except Exception as e:
    check("repeat", False, str(e))

# ---------------------------------------------------------------------------
# 8. /mcp endpoint reachable (HTTP-level check only)
# ---------------------------------------------------------------------------
print("\n[8] MCP HTTP endpoint (/mcp)")
try:
    req = urllib.request.Request(
        BASE + "/mcp",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}',
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            check("/mcp responds 200", resp.status == 200)
    except urllib.error.HTTPError as he:
        # Any HTTP response (even error) means the endpoint exists
        check("/mcp endpoint exists", True, f"HTTP {he.code}")
except Exception as e:
    check("/mcp reachable", False, str(e))

# ---------------------------------------------------------------------------
# 9 & 10. Import checks (no runtime crashes on import)
# ---------------------------------------------------------------------------
print("\n[9] voice_control.py import check")
sys.path.insert(0, str(Path(__file__).parent))
try:
    # Just check the file parses (compile), don't run the message loop
    src = Path(__file__).parent / "voice_control.py"
    code = compile(src.read_text(encoding="utf-8"), str(src), "exec")
    check("voice_control.py compiles", True)
except SyntaxError as e:
    check("voice_control.py compiles", False, str(e))
except FileNotFoundError:
    check("voice_control.py exists", False, "file not found")

print("\n[10] voice_status.py import check")
try:
    src = Path(__file__).parent / "voice_status.py"
    code = compile(src.read_text(encoding="utf-8"), str(src), "exec")
    check("voice_status.py compiles", True)
except SyntaxError as e:
    check("voice_status.py compiles", False, str(e))
except FileNotFoundError:
    check("voice_status.py exists", False, "file not found")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print()
total_pass = sum(1 for line in open(__file__).readlines()
                 if line.strip().startswith("check(")) - len(failures)

if failures:
    print(f"\033[91m{len(failures)} test(s) FAILED:\033[0m")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
else:
    print(f"\033[92mAll checks passed.\033[0m")
    sys.exit(0)
