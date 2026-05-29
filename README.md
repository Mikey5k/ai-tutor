# AI Tutor — Claude Code Powered Desktop Tutor

An AI tutor that watches your screen, controls your mouse and keyboard, speaks and listens, and teaches you anything from a YouTube course. Built around Claude Code as the brain, with six MCP servers as its senses and hands.

---

## STEP 0 — Check Your Hardware Before Anything Else

Run this on the new machine **before installing anything**. Minimum requirements differ based on whether you want local AI models or cloud APIs.

### Windows — paste this in PowerShell

```powershell
# CPU
$cpu = Get-WmiObject Win32_Processor | Select-Object Name, NumberOfCores, MaxClockSpeed
# RAM
$ram = [math]::Round((Get-WmiObject Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
# GPU
$gpu = Get-WmiObject Win32_VideoController | Select-Object Name, AdapterRAM
# Disk
$disk = Get-WmiObject Win32_LogicalDisk | Where-Object { $_.DriveType -eq 3 } |
    Select-Object DeviceID,
        @{N="Free_GB";E={[math]::Round($_.FreeSpace/1GB,1)}},
        @{N="Total_GB";E={[math]::Round($_.Size/1GB,1)}}

Write-Output "CPU: $($cpu.Name) — $($cpu.NumberOfCores) cores @ $($cpu.MaxClockSpeed) MHz"
Write-Output "RAM: $ram GB"
$gpu | ForEach-Object { Write-Output "GPU: $($_.Name) — $([math]::Round($_.AdapterRAM/1MB,0)) MB VRAM" }
$disk | ForEach-Object { Write-Output "Disk $($_.DeviceID): $($_.Free_GB) GB free / $($_.Total_GB) GB total" }
```

### Read your result against this table

| Spec | Minimum (cloud APIs) | Full local AI |
|---|---|---|
| RAM | **8 GB** | 16 GB |
| CPU | Any 4-core | Modern 6-core+ |
| GPU | Not needed | RTX 3060+ (12 GB VRAM) |
| Disk free | 5 GB | 30 GB (models) |
| Internet | Required | Optional |

### Choose your configuration mode based on the result

| Your RAM | Mode | What runs locally | What uses cloud APIs |
|---|---|---|---|
| Under 6 GB | **Cloud-first** | Nothing heavy | TTS, STT, vision, LLM all via API |
| 8–12 GB | **Hybrid** | Whisper STT (tiny/base), VAD | TTS, vision via API |
| 16 GB+ | **Full local** | Whisper, Kokoro TTS, Moondream | Only LLM (or Ollama too) |

Jump to the matching section in [Installation](#installation) below.

---

## Architecture Overview

```
Claude Code (the brain)
    │
    ├── mcp-servers/overlay/          Visual teaching aids (highlights, arrows, callouts)
    ├── mcp-servers/desktop-control/  Controls native Windows apps via UIA + PyAutoGUI
    ├── mcp-servers/browser-control/  Controls Chrome via Playwright CDP
    ├── mcp-servers/vision-fallback/  Screenshots → Gemini Flash / Moondream
    ├── mcp-servers/voice/            TTS + STT + VAD interrupt listener
    └── mcp-servers/lesson-manager/   Course ingestion, curriculum, student tracking
```

```
data/
├── courses/{course-id}/             Ingested course content, scripts, audio
└── students/{student-id}/           Student knowledge model, progress, session log
```

---

## Prerequisites

Install these on the new machine before anything else.

### 1. Python 3.10 or later

```
https://www.python.org/downloads/
```

Verify:
```powershell
python --version
```

### 2. Git

```
https://git-scm.com/downloads
```

### 3. Claude Code CLI

```powershell
npm install -g @anthropic-ai/claude-code
```

Or download the Windows installer from https://claude.ai/code

### 4. Playwright browsers (after pip install)

```powershell
playwright install chromium
```

### 5. Docker Desktop (optional — for SearXNG resource search)

```
https://www.docker.com/products/docker-desktop/
```

Start SearXNG for module resource fetching:
```powershell
docker run -d -p 8080:8080 --name searxng searxng/searxng
```

### 6. Ollama (optional — for local vision model)

```
https://ollama.com/
```

```powershell
ollama pull moondream
```

---

## Clone the Repo

```powershell
git clone https://github.com/Mikey5k/ai-tutor.git E:\ai-tutor
cd E:\ai-tutor
```

> **Why E:\?** The project uses absolute paths pointing to `E:\ai-tutor`. If you clone elsewhere, do a find-and-replace of `E:/ai-tutor` in `config.json` and all `server.py` files before proceeding.

---

## API Keys Setup

```powershell
copy .env.example .env
notepad .env
```

Fill in your keys:

| Key | Where to get it | Required? |
|---|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/ | Yes (or use OpenRouter) |
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey | Yes (vision fallback) |
| `OPENROUTER_API_KEY` | https://openrouter.ai/keys | Optional (replaces Anthropic key) |

### Using OpenRouter instead of Anthropic directly

If you have OpenRouter tokens instead of Anthropic API credits:

1. Set `OPENROUTER_API_KEY` in `.env`
2. Edit `mcp-servers/lesson-manager/curriculum/curriculum_builder.py` — change:
   ```python
   # From:
   self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
   
   # To:
   import openai
   self.client = openai.OpenAI(
       api_key=os.environ.get("OPENROUTER_API_KEY", ""),
       base_url="https://openrouter.ai/api/v1"
   )
   self.model = "anthropic/claude-sonnet-4-6"  # or "meta-llama/llama-3.1-8b-instruct" (free)
   ```
3. Do the same in `exercise_builder.py`, `test_builder.py`, `lesson_scripter.py`, and `lesson-manager/server.py` (evaluate_answer function)

---

## Installation

### Option A — Cloud-first (8 GB RAM or less)

Uses cloud APIs for TTS and STT. No local AI models loaded. Recommended for most machines.

**Step 1 — Install per-server dependencies**

```powershell
# Core servers (always install these)
pip install -r E:\ai-tutor\mcp-servers\desktop-control\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\browser-control\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\vision-fallback\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\overlay\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\lesson-manager\requirements.txt

# Voice server — cloud-friendly deps only
pip install mcp edge-tts sounddevice soundfile torch silero-vad pyaudio
```

**Step 2 — Swap Kokoro TTS for edge-tts**

Open `mcp-servers/voice/tts_engine.py` and replace the `initialize()` and `generate_audio()` methods:

```python
import asyncio
import edge_tts

def initialize(self):
    self._pipeline = "edge_tts"  # signals to use edge-tts

def generate_audio(self, text: str) -> str:
    cache_path = self._get_cache_path(text)
    if cache_path.exists():
        return str(cache_path)
    
    async def _gen():
        communicate = edge_tts.Communicate(text, voice="en-US-AriaNeural")
        await communicate.save(str(cache_path))
    
    asyncio.get_event_loop().run_until_complete(_gen())
    return str(cache_path)
```

**Step 3 — Use Whisper tiny instead of base**

Open `mcp-servers/voice/stt_engine.py`, change:
```python
def __init__(self, model_size: str = "base"):
# to:
def __init__(self, model_size: str = "tiny"):
```

**Step 4 — Install Playwright browsers**
```powershell
playwright install chromium
```

---

### Option B — Hybrid (8–12 GB RAM)

Keep local Whisper STT, use edge-tts for TTS (best balance).

```powershell
pip install -r E:\ai-tutor\mcp-servers\desktop-control\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\browser-control\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\vision-fallback\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\overlay\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\voice\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\lesson-manager\requirements.txt
pip install edge-tts

playwright install chromium
```

Then do Steps 2 from Option A (swap TTS to edge-tts). Keep Whisper base.

---

### Option C — Full local (16 GB+ RAM, GPU preferred)

Install everything as-is:

```powershell
pip install -r E:\ai-tutor\mcp-servers\desktop-control\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\browser-control\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\vision-fallback\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\voice\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\overlay\requirements.txt
pip install -r E:\ai-tutor\mcp-servers\lesson-manager\requirements.txt

playwright install chromium
ollama pull moondream
```

Kokoro TTS:
```powershell
pip install kokoro
python -c "from kokoro import KPipeline; KPipeline(lang_code='a')"
```

---

## Configure MCP Servers in Claude Code

The `.claude/settings.json` is already set up. Update API keys in it or load them from environment:

```powershell
# Set env vars before launching Claude Code
$env:ANTHROPIC_API_KEY = "your-key"
$env:GEMINI_API_KEY = "your-key"
$env:OPENROUTER_API_KEY = "your-key"
```

Or add them directly in `.claude/settings.json` under each server's `env` block.

---

## Running the System

### Full startup (all servers + Claude Code)

```powershell
E:\ai-tutor\startup.bat
```

This:
1. Starts all 6 MCP servers in background windows
2. Waits for each to pass its health check (`/health` endpoint)
3. Launches Claude Code with the project loaded

### Manual server test

```powershell
python E:\ai-tutor\tools\test_mcp_servers.py
```

Expected output:
```
overlay          127.0.0.1:9100  OK
desktop-control  127.0.0.1:9101  OK
browser-control  127.0.0.1:9102  OK
vision-fallback  127.0.0.1:9103  OK
voice            127.0.0.1:9104  OK
lesson-manager   127.0.0.1:9105  OK

6/6 servers healthy
```

### Ingest your first course

```powershell
python E:\ai-tutor\tools\ingest_course.py "https://www.youtube.com/watch?v=EXAMPLE" --title "Python Basics"
```

This downloads the transcript, generates curriculum, writes lesson scripts, exercises, tests, and pre-caches audio. Takes 5–15 minutes depending on course length.

---

## MCP Server Ports

| Server | Port | Purpose |
|---|---|---|
| overlay | 9100 | Visual highlights on screen |
| desktop-control | 9101 | UIA + mouse/keyboard |
| browser-control | 9102 | Chrome CDP via Playwright |
| vision-fallback | 9103 | Screenshots → vision model |
| voice | 9104 | TTS + STT + VAD |
| lesson-manager | 9105 | Courses, students, curriculum |

---

## Project Status and What to Build Next

### What is complete (all code written and on disk)

- All 6 MCP servers — full implementations, not stubs
- CLAUDE.md tutor persona and all behavior rules
- Lesson ingestion pipeline (yt-dlp → transcript → curriculum → scripts → exercises → tests → resources)
- Student model with knowledge tracking and adaptive difficulty
- SQLite schema and file-based cache
- Overlay renderer (Win32 GDI, 30fps, click-through)
- Voice pipeline (TTS + STT + Silero VAD + interrupt handler)
- Browser control (Playwright CDP + DOM parser + AX tree parser)
- Desktop control (UIA + PyAutoGUI + app launcher)
- Startup script, health checks, CLI tools

### What is NOT done yet (next steps in order)

1. **End-to-end smoke test** — start all servers, ask Claude Code to open Notepad, highlight the title bar, type text, narrate it. This validates the overlay + desktop-control + voice pipeline together.

2. **Voice interrupt latency test** — speak a question mid-lesson and measure how long until Claude Code responds. Target: under 2 seconds. Tune Whisper model size and VAD threshold based on result.

3. **Course ingestion test** — run `ingest_course.py` against a real short YouTube video (5–10 min). Check that `curriculum.json`, `script.json`, `exercises.json`, `test.json` are all created and look correct.

4. **Full module run** — load a generated script and play it through with `speak()` calls, checking that audio caching works and the student model updates after the test.

5. **OpenRouter integration** (if not using Anthropic API directly) — swap the `anthropic` SDK calls in the lesson-manager curriculum files to use `openai` SDK pointed at OpenRouter.

6. **edge-tts integration** (if on limited RAM) — replace Kokoro TTS calls in `tts_engine.py` with `edge-tts` streaming.

7. **SearXNG** — spin up Docker container, test `resource_fetcher.py` produces quality results.

8. **Error recovery** — handle app crashes, unexpected UI states, UIA returning empty, network failures mid-lesson.

9. **Session logging and replay** — build tooling to replay a session log for debugging lesson flow issues.

### Build priority order (from the original plan)

- Weeks 1–2: Overlay + Desktop Control (DONE — code written)
- Weeks 3–4: Voice (DONE — code written)
- Weeks 5–6: Browser Control (DONE — code written)
- Weeks 7–10: Lesson Manager ingestion + generation (DONE — code written)
- Weeks 11–12: Student model + adaptive difficulty (DONE — code written)
- Weeks 13–16: Integration testing, latency tuning, error recovery (NOT DONE)

---

## Continuing with Claude Code on a New Machine

When you open this project with Claude Code on the new machine:

```powershell
cd E:\ai-tutor
claude
```

Claude Code will read `CLAUDE.md` automatically and load the tutor persona. For development work (not tutoring), tell Claude Code:

> "We're in dev mode — building and debugging the AI tutor system. Read README.md for full context on what's built and what's next."

### Key files for Claude Code context

| File | Purpose |
|---|---|
| `CLAUDE.md` | Tutor behavior rules (loaded automatically) |
| `README.md` | Full system context and next steps (this file) |
| `config.json` | Ports, model names, paths |
| `.claude/settings.json` | MCP server registrations |
| `mcp-servers/*/server.py` | Each server's tool definitions |
| `data/courses/` | Generated course content after ingestion |
| `data/students/` | Student model JSON files |

### Recommended first Claude Code session on new machine

1. Run `python tools/test_mcp_servers.py` — see which servers start
2. Fix any server that fails (usually a missing pip package)
3. Run the Notepad smoke test: ask Claude Code to open Notepad and highlight the title bar
4. If that works, ingest a short test video and run a module

---

## Troubleshooting

### Server won't start — missing module

```powershell
pip install -r E:\ai-tutor\mcp-servers\<server-name>\requirements.txt
```

### UIA queries return empty tree

The app may have UIA disabled. Switch to vision-fallback for that app. Some apps (e.g. Electron apps, games) don't expose UIA at all.

### Playwright can't connect to Chrome

Chrome must be started with `--remote-debugging-port=9222`. The browser-control server does this automatically, but if Chrome is already running without that flag:
1. Close all Chrome windows
2. Let the browser-control server launch it

### Voice VAD fires constantly (false positives)

Increase VAD threshold in `config.json`:
```json
"vad_threshold": 0.7
```

Or reduce ambient noise — the Silero model is sensitive to fan/AC noise in quiet rooms.

### Whisper transcription is too slow

Switch to a smaller model in `stt_engine.py`:
```python
def __init__(self, model_size: str = "tiny"):  # tiny = 150MB, fast
```

### Out of memory with all servers running

Stop the vision server if you're not testing canvas content — it has the largest optional dependencies. The four servers you always need are: overlay, desktop-control, voice, lesson-manager.

---

## Token Cost Reference

| Operation | Model | Approx cost |
|---|---|---|
| Ingest 1-hour course (curriculum) | Claude Sonnet | ~$0.10 |
| Generate 1 module script | Claude Sonnet | ~$0.05 |
| Generate exercises + test per module | Claude Haiku | ~$0.01 |
| Evaluate 1 student answer | Claude Haiku | ~$0.001 |
| Vision description (screenshot) | Gemini Flash | ~$0.0003 |
| TTS via edge-tts | Microsoft Edge | Free |
| STT via local Whisper | Local | Free |
| SearXNG resource search | Self-hosted | Free |

A typical full course ingestion (10 modules, 1 hour video): **~$1.00 total**.
A live tutoring session (1 hour): **~$0.05–0.20** depending on interruptions and vision calls.

---

## License

MIT
