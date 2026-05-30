#!/usr/bin/env python3
"""
AI Tutor — Technical Interview Demo
Aria Neural interviews you about your programming background and AI knowledge.
Single process, no overlapping audio. Interrupt by speaking at any time.

Run: python E:\ai-tutor\tools\interview_test.py
"""

import asyncio, threading, queue, time, io, sys, re
import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy import signal

# ── dependency check ───────────────────────────────────────────────────────────
for pkg in ["edge_tts", "faster_whisper", "sounddevice", "soundfile", "torch", "scipy"]:
    import importlib
    try:
        importlib.import_module(pkg.replace("-", "_"))
    except ImportError:
        print(f"Missing: {pkg}  —  pip install {pkg}")
        sys.exit(1)

import edge_tts
from faster_whisper import WhisperModel
import torch

# ── config ─────────────────────────────────────────────────────────────────────
VOICE               = "en-US-AriaNeural"
SAMPLE_RATE         = 16000
VAD_THRESHOLD       = 0.45
INTERRUPT_THRESHOLD = 0.72
INTERRUPT_DELAY     = 0.40
ECHO_GATE_RATIO     = 1.5
SILENCE_END_MS      = 700

# ── WASAPI device finder ───────────────────────────────────────────────────────
def find_wasapi_devices():
    try:
        hostapis = sd.query_hostapis()
        wasapi_host = next((i for i, h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
        if wasapi_host is None:
            return None, None
        devices = sd.query_devices()
        in_dev = out_dev = None
        for i, d in enumerate(devices):
            if d["hostapi"] == wasapi_host:
                if d["max_input_channels"] > 0 and in_dev is None:
                    in_dev = i
                if d["max_output_channels"] > 0 and out_dev is None:
                    out_dev = i
        return in_dev, out_dev
    except Exception:
        return None, None

WASAPI_IN, WASAPI_OUT = find_wasapi_devices()

# ── DSP ────────────────────────────────────────────────────────────────────────
def enhance(audio: np.ndarray, sr: int) -> np.ndarray:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float64)
    sos = signal.butter(2, 80, "hp", fs=sr, output="sos")
    audio = signal.sosfilt(sos, audio)
    sos2 = signal.butter(2, [2000, 5000], "bandpass", fs=sr, output="sos")
    audio += signal.sosfilt(sos2, audio) * 0.13
    sos3 = signal.butter(2, [300, 800], "bandpass", fs=sr, output="sos")
    audio += signal.sosfilt(sos3, audio) * 0.06
    thr = 0.58
    mask = np.abs(audio) > thr
    audio[mask] = np.sign(audio[mask]) * (thr + (np.abs(audio[mask]) - thr) / 3.2)
    n7 = int(sr * 0.007); n13 = int(sr * 0.013)
    r1 = np.zeros_like(audio); r1[n7:]  = audio[:-n7]  * 0.085
    r2 = np.zeros_like(audio); r2[n13:] = audio[:-n13] * 0.038
    audio = audio + r1 + r2
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.87
    return audio.astype(np.float32)

# ── SSML builder ───────────────────────────────────────────────────────────────
def build_ssml(text: str, style: str = "friendly", style_degree: str = "1.1",
               rate: str = "-5%") -> str:
    escaped = (text
               .replace("&", "&amp;").replace('"', "&quot;")
               .replace("'", "&apos;").replace("<", "&lt;").replace(">", "&gt;"))
    escaped = re.sub(r'\.(\s+)', r'.<break time="230ms"/>\1', escaped)
    escaped = re.sub(r'\?(\s+)', r'?<break time="350ms"/>\1', escaped)
    escaped = re.sub(r'!(\s+)',  r'!<break time="280ms"/>\1', escaped)
    escaped = re.sub(r',(\s+)',  r',<break time="120ms"/>\1', escaped)
    escaped = re.sub(r':(\s+)',  r':<break time="170ms"/>\1', escaped)
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
        f'<voice name="{VOICE}">'
        f'<mstts:express-as style="{style}" styledegree="{style_degree}">'
        f'<prosody rate="{rate}">{escaped}</prosody>'
        f'</mstts:express-as></voice></speak>'
    )

# ── TTS: synthesize + DSP ──────────────────────────────────────────────────────
async def synthesize(text: str, style: str = "friendly", rate: str = "-5%") -> tuple:
    ssml = build_ssml(text, style=style, rate=rate)
    communicate = edge_tts.Communicate(ssml, voice=VOICE)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    if not chunks:
        raise RuntimeError("edge-tts returned no audio")
    audio, sr = sf.read(io.BytesIO(b"".join(chunks)), dtype="float32")
    return enhance(audio, sr), sr

# ── Player with interrupt support ──────────────────────────────────────────────
class Player:
    def __init__(self):
        self.playback_rms = 0.0
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def play(self, audio: np.ndarray, sr: int) -> bool:
        """Play audio. Returns True=finished, False=interrupted."""
        self._stop.clear()
        chunk_size = int(sr * 0.025)
        armed_at = time.time() + INTERRUPT_DELAY
        interrupted = False

        out_kwargs = {"samplerate": sr, "channels": 1}
        if WASAPI_OUT is not None:
            out_kwargs["device"] = WASAPI_OUT

        with sd.OutputStream(**out_kwargs) as stream:
            for start in range(0, len(audio), chunk_size):
                if self._stop.is_set():
                    interrupted = True
                    break
                chunk = audio[start:start + chunk_size]
                if len(chunk) < chunk_size:
                    chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
                self.playback_rms = float(np.sqrt(np.mean(chunk ** 2)))
                stream.write(chunk.reshape(-1, 1))
                if time.time() < armed_at:
                    pass

        self.playback_rms = 0.0
        return not interrupted

# ── STT ────────────────────────────────────────────────────────────────────────
_whisper = None
def get_whisper():
    global _whisper
    if _whisper is None:
        print("  Loading Whisper tiny...")
        _whisper = WhisperModel("tiny", device="cpu", compute_type="int8")
        print("  Whisper ready.")
    return _whisper

def transcribe(audio_np: np.ndarray) -> str:
    model = get_whisper()
    segs, _ = model.transcribe(audio_np, language="en", beam_size=1,
                                vad_filter=True, vad_parameters={"min_silence_duration_ms": 300})
    return " ".join(s.text.strip() for s in segs).strip()

# ── VAD listener: listens and returns transcription ───────────────────────────
def listen(player: Player, timeout: float = 20.0) -> str:
    """Listen for speech, return transcription. Empty string = timeout/silence."""
    vad_model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad", model="silero_vad", verbose=False
    )
    (get_speech_timestamps, _, read_audio, _, _) = utils

    frames: list = []
    speech_frames: list = []
    in_speech = False
    silence_count = 0
    silence_chunks = int(SILENCE_END_MS / 30)
    chunk_samples = int(SAMPLE_RATE * 0.030)
    started_at = time.time()

    in_kwargs = {"samplerate": SAMPLE_RATE, "channels": 1, "dtype": "float32",
                 "blocksize": chunk_samples}
    if WASAPI_IN is not None:
        in_kwargs["device"] = WASAPI_IN

    result_q: queue.Queue = queue.Queue()

    def _cb(indata, frames_count, t, status):
        flat = indata[:, 0].copy()
        mic_rms = float(np.sqrt(np.mean(flat ** 2)))

        # Echo gate: during playback, suppress if mic level matches expected echo
        if player.playback_rms > 0:
            echo_est = player.playback_rms * 0.45
            if mic_rms < echo_est * ECHO_GATE_RATIO:
                return

        threshold = INTERRUPT_THRESHOLD if player.playback_rms > 0 else VAD_THRESHOLD

        with torch.no_grad():
            t16 = torch.from_numpy(flat)
            conf = vad_model(t16, SAMPLE_RATE).item()

        nonlocal in_speech, silence_count
        if conf > threshold:
            if not in_speech:
                in_speech = True
                silence_count = 0
                print("  [listening...]", end="", flush=True)
            speech_frames.append(flat)
            if player.playback_rms > 0:
                player.stop()
        elif in_speech:
            speech_frames.append(flat)
            silence_count += 1
            if silence_count >= silence_chunks:
                result_q.put("done")

    with sd.InputStream(**in_kwargs, callback=_cb):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result_q.get(timeout=0.1)
                break
            except queue.Empty:
                if time.time() >= deadline:
                    break

    if not speech_frames:
        return ""
    audio_np = np.concatenate(speech_frames)
    print(" transcribing...", end="", flush=True)
    text = transcribe(audio_np)
    print(f" got: {text!r}")
    return text

# ── Interview script ───────────────────────────────────────────────────────────
QUESTIONS = [
    {
        "text": (
            "Welcome! I'm your AI tutor, Aria. "
            "Let's get to know each other before we dive in. "
            "First question: how long have you been programming, "
            "and which language did you start with?"
        ),
        "style": "friendly",
        "rate": "-4%",
    },
    {
        "text": (
            "That's a solid start. "
            "Now, when you hear the term 'machine learning', "
            "what comes to mind? "
            "Just describe it in your own words — no right or wrong answer here."
        ),
        "style": "curious",
        "rate": "-5%",
    },
    {
        "text": (
            "Interesting perspective. "
            "Have you ever trained a model yourself, "
            "even something simple like a linear regression or a small neural network? "
            "Walk me through what you did and what happened."
        ),
        "style": "friendly",
        "rate": "-5%",
    },
    {
        "text": (
            "Good. Last question: "
            "if you could learn one specific thing about AI in our sessions together — "
            "one concept, one technique, one tool — "
            "what would it be, and why does it matter to you?"
        ),
        "style": "hopeful",
        "rate": "-6%",
    },
]

FEEDBACK_TEMPLATES = {
    "good": [
        "That's a thoughtful answer. You clearly have some real experience to build on.",
        "Nice. That kind of hands-on intuition is exactly what we'll be expanding.",
        "I like that. You've got the right instincts — let's sharpen them.",
    ],
    "brief": [
        "Got it. Brief but honest, and that's fine — we'll fill in the gaps together.",
        "Understood. No worries — that's exactly what we're here to work through.",
        "Fair enough. Everyone starts somewhere, and this is a great place to begin.",
    ],
    "closing": (
        "I appreciate you sharing all of that. "
        "Based on what you've told me, I have a clear picture of where you are "
        "and where we can take you. "
        "Our sessions will be practical and grounded — "
        "you'll write real code, see real models, and understand the why behind everything. "
        "Looking forward to working with you."
    ),
}

import random

def pick_feedback(answer: str) -> tuple[str, str]:
    """Return (feedback_text, style)."""
    words = len(answer.split())
    if words < 8:
        return random.choice(FEEDBACK_TEMPLATES["brief"]), "empathetic"
    return random.choice(FEEDBACK_TEMPLATES["good"]), "cheerful"

# ── Main interview loop ────────────────────────────────────────────────────────
async def run_interview():
    player = Player()

    print()
    print("=" * 50)
    print("  AI Tutor — Technical Interview")
    print("  Voice: Aria Neural + SSML + DSP")
    if WASAPI_IN is not None:
        print(f"  Mic:   WASAPI device [{WASAPI_IN}]")
    if WASAPI_OUT is not None:
        print(f"  Out:   WASAPI device [{WASAPI_OUT}]")
    print("  Speak naturally. Interrupt at any time.")
    print("=" * 50)
    print()

    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n--- Question {i}/{len(QUESTIONS)} ---")
        print(f"  Aria: {q['text'][:60]}...")

        audio, sr = await synthesize(q["text"], style=q["style"], rate=q["rate"])

        # Start playback in a thread, listen concurrently for interrupt
        play_done = threading.Event()
        play_result = [True]

        def _play():
            play_result[0] = player.play(audio, sr)
            play_done.set()

        play_thread = threading.Thread(target=_play, daemon=True)
        play_thread.start()

        # Listen while question is playing (catches interrupt) then after it ends
        answer = listen(player, timeout=25.0)
        play_done.wait(timeout=1.0)

        if not answer:
            print("  (no answer heard — moving on)")
            answer = "no response"

        # Give feedback
        feedback, fb_style = pick_feedback(answer)
        print(f"\n  Aria feedback: {feedback[:60]}...")
        fb_audio, fb_sr = await synthesize(feedback, style=fb_style)
        player.play(fb_audio, fb_sr)
        time.sleep(0.3)

    # Closing
    print("\n--- Closing ---")
    closing = FEEDBACK_TEMPLATES["closing"]
    print(f"  Aria: {closing[:60]}...")
    cl_audio, cl_sr = await synthesize(closing, style="friendly", rate="-5%")
    player.play(cl_audio, cl_sr)

    print("\n  Interview complete.")
    print()

if __name__ == "__main__":
    asyncio.run(run_interview())
