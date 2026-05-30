#!/usr/bin/env python3
"""
Real-time Voice Q&A — enhanced build v2
TTS  : edge-tts + SSML + DSP (high-pass, presence, warmth, compression, room)
STT  : faster-whisper tiny  (~400ms, local)
VAD  : Silero + echo energy gate
Audio: WASAPI devices
LLM  : Claude Haiku via Anthropic / OpenRouter / Gemini fallback

v2 additions:
  - Thinking fillers (plays "Hmm, one sec..." while LLM generates)
  - Per-sentence prosody rate variation (±4% random per sentence)
  - Micro-timing randomization on all break durations
  - Emotion detection -> mstts:express-as style per response
  - Occasional false starts (~12%) for human-like hesitation
"""

import asyncio, threading, queue, time, os, io, sys, re, tempfile, hashlib, random
import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy import signal
from pathlib import Path

# ── dependency check ───────────────────────────────────────────────────────────
def _require(pkg, import_as=None):
    import importlib
    try:
        importlib.import_module(import_as or pkg)
    except ImportError:
        print(f"Missing: {pkg}  -  pip install {pkg}")
        sys.exit(1)

for _p, _i in [("edge_tts","edge_tts"),("faster_whisper","faster_whisper"),
               ("sounddevice",None),("soundfile",None),("torch",None),("scipy",None)]:
    _require(_p, _i)

import edge_tts
from faster_whisper import WhisperModel
import torch

# ── config ─────────────────────────────────────────────────────────────────────
VOICE               = "en-US-AriaNeural"
SAMPLE_RATE         = 16000
VAD_THRESHOLD       = 0.50
INTERRUPT_THRESHOLD = 0.78
INTERRUPT_DELAY     = 0.45
ECHO_GATE_RATIO     = 1.6
SILENCE_END_MS      = 650
WHISPER_MODEL       = "tiny"
MAX_HISTORY         = 16

SYSTEM_PROMPT = (
    "You are a friendly AI tutor in a real-time spoken conversation. "
    "Rules: answer in 2-4 natural spoken sentences - longer only when complexity demands. "
    "No bullet points, no markdown, no headers. "
    "No 'As an AI' or 'I should mention'. "
    "Use casual language: 'let's', 'here's the thing', 'think of it like'. "
    "When explaining code, describe it in plain words first, then mention syntax. "
    "Speak like a knowledgeable friend."
)

# ── thinking fillers ───────────────────────────────────────────────────────────
# (text, mstts_style, styledegree)
FILLERS = [
    ("Hmm, let me think about that.",    "thoughtful",  "1.0"),
    ("Right, give me just a moment.",    "calm",        "0.9"),
    ("Good question, one second.",       "friendly",    "1.1"),
    ("Interesting. Just a sec.",         "curious",     "1.0"),
    ("Let me think through that.",       "thoughtful",  "1.0"),
    ("Okay, working on it.",             "calm",        "0.9"),
    ("That's a good one, hold on.",      "friendly",    "1.1"),
    ("Hmm. Give me a second.",           "thoughtful",  "1.0"),
]

# ── false-start prefixes (~12% of responses) ──────────────────────────────────
_FALSE_STARTS = [
    "Right - ", "So - ", "Well, ", "I mean - ",
    "Yeah, so - ", "Okay, ", "Alright, ",
]

def maybe_false_start(text: str) -> str:
    if random.random() < 0.12 and text:
        prefix = random.choice(_FALSE_STARTS)
        first  = text[0].lower() if text[0].isupper() else text[0]
        return prefix + first + text[1:]
    return text

# ── emotion detection ──────────────────────────────────────────────────────────
_EMOTION_RULES = [
    (["wrong", "mistake", "confused", "struggle", "difficult", "hard",
      "don't understand", "not sure", "not quite", "actually no"],     "empathetic",      "1.0"),
    (["exactly", "perfect", "correct", "great", "well done",
      "nice work", "you got it", "that's right", "spot on"],           "cheerful",        "1.2"),
    (["interesting", "fascinating", "curious", "wonder",
      "here's the thing", "think about this", "actually"],             "curious",         "1.0"),
    (["def ", "class ", "function", "variable", "algorithm",
      "syntax", "loop", "recursion", "parameter", "import "],          "newscast-casual", "1.0"),
]

def detect_emotion(text: str) -> tuple:
    lower = text.lower()
    for keywords, style, degree in _EMOTION_RULES:
        if any(kw in lower for kw in keywords):
            return style, degree
    return "friendly", "1.0"

# ── WASAPI device finder ───────────────────────────────────────────────────────
def find_wasapi_devices():
    try:
        hostapis  = sd.query_hostapis()
        wh        = next((i for i, h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
        if wh is None:
            return None, None
        devices   = sd.query_devices()
        in_dev    = out_dev = None
        for i, d in enumerate(devices):
            if d["hostapi"] == wh:
                if d["max_input_channels"]  > 0 and in_dev  is None: in_dev  = i
                if d["max_output_channels"] > 0 and out_dev is None: out_dev = i
        return in_dev, out_dev
    except Exception:
        return None, None

WASAPI_IN, WASAPI_OUT = find_wasapi_devices()

# ── text processing ────────────────────────────────────────────────────────────
# NOTE: edge-tts SSML support is broken on this platform — any SSML wrapper
# causes the audio to run at ~8x slower than plain text synthesis.
# We use plain text + Communicate(rate=...) + manual silence concatenation instead.

_CODE_KEYWORDS = {
    "def ", "class ", "import ", "return ", "async ", "await ", "lambda ",
    "yield ", "function", "variable", "parameter", "argument", "syntax",
    "loop", "iterate", "recursion", "algorithm", "method", "attribute",
    "boolean", "integer", "string", "array", "dictionary", "tuple",
}
_SENTENCE_SPLIT  = re.compile(r'(?<=[.!?])\s+')
_SENTENCE_ENDER  = re.compile(r'([.!?])\s*$')

# Emotion -> rate mapping (controls speaking pace as proxy for emotion)
_EMOTION_RATES = {
    "cheerful":       "+5%",   # upbeat, slightly faster
    "empathetic":     "-15%",  # slower, more considered
    "curious":        "-7%",   # slightly slower, thoughtful
    "friendly":       "-5%",   # natural, warm
    "newscast-casual":"+0%",   # neutral, clear
    "thoughtful":     "-12%",  # slow, measured
    "calm":           "-8%",   # relaxed
    "hopeful":        "-5%",   # warm
}

def get_rate(text: str, emotion: str = "friendly", for_filler: bool = False) -> str:
    if for_filler:
        return "+5%"
    lower     = text.lower()
    code_hits = sum(1 for kw in _CODE_KEYWORDS if kw in lower)
    if code_hits >= 3:
        return "-15%"
    if code_hits >= 1:
        return "-10%"
    return _EMOTION_RATES.get(emotion, "-5%")

# ── DSP pipeline ───────────────────────────────────────────────────────────────
class AudioEnhancer:
    def enhance(self, audio: np.ndarray, sr: int) -> np.ndarray:
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float64)
        sos_hp   = signal.butter(2, 80, "hp", fs=sr, output="sos")
        audio    = signal.sosfilt(sos_hp, audio)
        sos_bp   = signal.butter(2, [2000, 5000], "bandpass", fs=sr, output="sos")
        audio   += signal.sosfilt(sos_bp, audio) * 0.13
        sos_warm = signal.butter(2, [300, 800], "bandpass", fs=sr, output="sos")
        audio   += signal.sosfilt(sos_warm, audio) * 0.06
        thr      = 0.58
        mask     = np.abs(audio) > thr
        audio[mask] = np.sign(audio[mask]) * (thr + (np.abs(audio[mask]) - thr) / 3.2)
        n7  = int(sr * 0.007); n13 = int(sr * 0.013)
        r1  = np.zeros_like(audio); r1[n7:]  = audio[:-n7]  * 0.085
        r2  = np.zeros_like(audio); r2[n13:] = audio[:-n13] * 0.038
        audio = audio + r1 + r2
        peak  = np.max(np.abs(audio))
        if peak > 0:
            audio = audio / peak * 0.87
        return audio.astype(np.float32)

# ── TTS ────────────────────────────────────────────────────────────────────────
class TTSEngine:
    def __init__(self):
        self.enhancer       = AudioEnhancer()
        self._cache         = Path("E:/ai-tutor/data/cache/audio")
        self._cache.mkdir(parents=True, exist_ok=True)
        self._filler_cache: dict = {}

    async def _synth_plain(self, text: str, rate: str) -> tuple:
        """Synthesize one plain-text fragment. Returns (audio float32, sr)."""
        communicate = edge_tts.Communicate(text.strip(), voice=VOICE, rate=rate)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        if not chunks:
            return np.zeros(100, dtype=np.float32), 24000
        return sf.read(io.BytesIO(b"".join(chunks)), dtype="float32")

    async def synthesize(self, text: str, emotion: str = None) -> tuple:
        """Split into sentences, synthesize each, join with randomized silence."""
        if emotion is None:
            emotion, _ = detect_emotion(text)
        key        = hashlib.md5(f"v3:{VOICE}:{emotion}:{text}".encode()).hexdigest()
        cache_path = self._cache / f"{key}.wav"
        if cache_path.exists():
            audio, sr = sf.read(str(cache_path), dtype="float32")
            return audio, sr

        rate      = get_rate(text, emotion)
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()] or [text]
        parts: list = []
        sr          = None

        for i, sent in enumerate(sentences):
            frag, frag_sr = await self._synth_plain(sent, rate)
            if sr is None:
                sr = frag_sr
            parts.append(frag)

            if i < len(sentences) - 1:
                m = _SENTENCE_ENDER.search(sent)
                if m and m.group(1) == "?":
                    gap_ms = random.randint(290, 370)
                elif m and m.group(1) == "!":
                    gap_ms = random.randint(240, 300)
                else:
                    gap_ms = random.randint(200, 260)
                parts.append(np.zeros(int(frag_sr * gap_ms / 1000), dtype=np.float32))

        combined = np.concatenate(parts) if parts else np.zeros(100, dtype=np.float32)
        combined = self.enhancer.enhance(combined, sr or 24000)
        sf.write(str(cache_path), combined, sr or 24000)
        return combined, sr or 24000

    async def synthesize_filler(self, text: str, filler_emotion: str,
                                _degree: str) -> tuple:
        if text in self._filler_cache:
            return self._filler_cache[text]
        rate  = get_rate(text, filler_emotion, for_filler=True)
        audio, sr = await self._synth_plain(text, rate)
        audio = self.enhancer.enhance(audio, sr)
        self._filler_cache[text] = (audio, sr)
        return audio, sr

    async def warm_fillers(self):
        print("  Pre-caching thinking fillers...")
        for text, style, degree in FILLERS:
            await self.synthesize_filler(text, style, degree)
        print("  Fillers ready.")

# ── playback ───────────────────────────────────────────────────────────────────
class Player:
    def __init__(self):
        self._stop        = threading.Event()
        self._armed       = threading.Event()
        self.playback_rms = 0.0
        # Resample to device native SR to avoid PortAudio paInvalidSampleRate
        if WASAPI_OUT is not None:
            self._native_sr = int(sd.query_devices(WASAPI_OUT)["default_samplerate"])
        else:
            self._native_sr = 48000

    def _resample(self, audio: np.ndarray, src_sr: int) -> tuple:
        if src_sr == self._native_sr:
            return audio, self._native_sr
        from math import gcd
        g   = gcd(self._native_sr, src_sr)
        up  = self._native_sr // g
        dn  = src_sr // g
        out = signal.resample_poly(audio.astype(np.float64), up, dn)
        return out.astype(np.float32), self._native_sr

    def interrupt(self):
        if self._armed.is_set():
            self._stop.set()

    def force_stop(self):
        self._stop.set()

    def play(self, audio: np.ndarray, sr: int) -> bool:
        """Play audio. Returns True=finished, False=interrupted/force-stopped."""
        audio, sr = self._resample(audio, sr)

        self._stop.clear()
        self._armed.clear()
        self.playback_rms = 0.0

        CHUNK = int(sr * 0.04)
        mono  = audio if audio.ndim == 1 else audio.mean(axis=1)
        pos   = 0

        def _arm():
            time.sleep(INTERRUPT_DELAY)
            self._armed.set()
        threading.Thread(target=_arm, daemon=True).start()

        try:
            with sd.OutputStream(
                samplerate=sr, channels=1, dtype="float32",
                device=WASAPI_OUT
            ) as stream:
                while pos < len(mono):
                    if self._stop.is_set():
                        return False
                    end   = min(pos + CHUNK, len(mono))
                    chunk = mono[pos:end]
                    self.playback_rms = float(np.sqrt(np.mean(chunk ** 2)))
                    stream.write(chunk.reshape(-1, 1))
                    pos = end
        except Exception as e:
            print(f"[playback error: {e}]")
        finally:
            self._armed.clear()
            self.playback_rms = 0.0
        return True

# ── VAD + mic listener ─────────────────────────────────────────────────────────
class VADListener:
    def __init__(self, speech_queue: queue.Queue, player: Player):
        self.speech_queue = speech_queue
        self.player       = player
        self._model       = None
        self._enabled     = True

    def initialize(self):
        print("  Loading Silero VAD...")
        self._model, _ = torch.hub.load(
            "snakers4/silero-vad", "silero_vad",
            force_reload=False, trust_repo=True
        )
        self._model.eval()

    def set_enabled(self, v: bool):
        self._enabled = v

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _vad_score(self, chunk: np.ndarray) -> float:
        with torch.no_grad():
            return self._model(torch.from_numpy(chunk).float(), SAMPLE_RATE).item()

    def _loop(self):
        CHUNK_N     = int(SAMPLE_RATE * 0.030)
        silence_end = int(SILENCE_END_MS / 30)
        buffer      = []
        in_speech   = False
        silence_cnt = 0

        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1,
            dtype="float32", blocksize=CHUNK_N,
            device=WASAPI_IN
        ) as stream:
            while True:
                chunk, _ = stream.read(CHUNK_N)
                flat = chunk.flatten()

                if not self._enabled:
                    buffer, in_speech, silence_cnt = [], False, 0
                    continue

                if self.player._armed.is_set():
                    mic_rms  = float(np.sqrt(np.mean(flat ** 2)))
                    echo_est = self.player.playback_rms * 0.45
                    if mic_rms < echo_est * ECHO_GATE_RATIO:
                        continue
                    thresh = INTERRUPT_THRESHOLD
                else:
                    thresh = VAD_THRESHOLD

                score = self._vad_score(flat)

                if score >= thresh:
                    if not in_speech:
                        in_speech = True
                    buffer.append(flat)
                    silence_cnt = 0
                    self.player.interrupt()
                elif in_speech:
                    buffer.append(flat)
                    silence_cnt += 1
                    if silence_cnt >= silence_end:
                        captured = np.concatenate(buffer)
                        if len(captured) > SAMPLE_RATE * 0.3:
                            self.speech_queue.put(captured)
                        buffer, in_speech, silence_cnt = [], False, 0

# ── STT ────────────────────────────────────────────────────────────────────────
class STTEngine:
    def __init__(self):
        self._model = None

    def initialize(self):
        print(f"  Loading Whisper {WHISPER_MODEL}...")
        self._model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            sf.write(f.name, audio, SAMPLE_RATE)
            path = f.name
        try:
            segs, _ = self._model.transcribe(
                path, beam_size=3, language="en", vad_filter=True
            )
            return " ".join(s.text for s in segs).strip()
        finally:
            os.unlink(path)

# ── LLM ────────────────────────────────────────────────────────────────────────
class LLMClient:
    def __init__(self):
        self._client  = None
        self._mode    = None
        self._model   = None
        self._history = []

    def initialize(self):
        or_key  = os.environ.get("OPENROUTER_API_KEY", "").strip()
        ant_key = os.environ.get("ANTHROPIC_API_KEY",  "").strip()
        gem_key = os.environ.get("GEMINI_API_KEY",     "").strip()

        if or_key:
            import openai
            self._client = openai.OpenAI(api_key=or_key,
                                         base_url="https://openrouter.ai/api/v1")
            self._mode  = "openrouter"
            self._model = "anthropic/claude-haiku-4-5"
            print("  LLM: Claude Haiku via OpenRouter")
        elif ant_key:
            import anthropic as _ant
            self._client = _ant.Anthropic(api_key=ant_key)
            self._mode   = "anthropic"
            self._model  = "claude-haiku-4-5-20251001"
            print("  LLM: Claude Haiku via Anthropic")
        elif gem_key:
            import google.generativeai as genai
            genai.configure(api_key=gem_key)
            self._client = genai.GenerativeModel(
                "gemini-1.5-flash", system_instruction=SYSTEM_PROMPT
            )
            self._mode = "gemini"
            print("  LLM: Gemini Flash via Google AI Studio")
        else:
            print("\nNo API key found. Set OPENROUTER_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY")
            sys.exit(1)

    def ask(self, text: str) -> str:
        self._history.append({"role": "user", "content": text})

        if self._mode == "openrouter":
            resp  = self._client.chat.completions.create(
                model=self._model, max_tokens=380,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          *self._history]
            )
            reply = resp.choices[0].message.content.strip()
        elif self._mode == "anthropic":
            import anthropic as _ant
            resp  = self._client.messages.create(
                model=self._model, max_tokens=380,
                system=SYSTEM_PROMPT, messages=self._history
            )
            reply = resp.content[0].text.strip()
        else:
            hist  = [{"role": m["role"], "parts": [m["content"]]}
                     for m in self._history[:-1]]
            reply = self._client.start_chat(history=hist).send_message(text).text.strip()

        self._history.append({"role": "assistant", "content": reply})
        if len(self._history) > MAX_HISTORY * 2:
            self._history = self._history[-(MAX_HISTORY * 2):]
        return reply

# ── main ───────────────────────────────────────────────────────────────────────
class VoiceQA:
    def __init__(self):
        self.tts      = TTSEngine()
        self.player   = Player()
        self.stt      = STTEngine()
        self.llm      = LLMClient()
        self.speech_q = queue.Queue()
        self.vad      = VADListener(self.speech_q, self.player)

    def initialize(self):
        print("\nInitializing Voice Q&A (v2)\n")
        in_name  = sd.query_devices(WASAPI_IN)["name"]  if WASAPI_IN  is not None else "default"
        out_name = sd.query_devices(WASAPI_OUT)["name"] if WASAPI_OUT is not None else "default"
        print(f"  Audio in : {in_name}")
        print(f"  Audio out: {out_name}")
        print(f"  Features : fillers, per-sentence rate, micro-timing, emotion, false-starts")
        print()
        self.stt.initialize()
        self.vad.initialize()
        self.llm.initialize()

    async def speak(self, text: str, emotion: str = None) -> bool:
        audio, sr = await self.tts.synthesize(text, emotion)
        return self.player.play(audio, sr)

    async def _turn(self, user_text: str) -> tuple:
        """Run one Q&A turn with concurrent filler + LLM call."""
        loop = asyncio.get_event_loop()

        # Start LLM call in thread (non-blocking for asyncio)
        llm_task = loop.run_in_executor(None, self.llm.ask, user_text)

        # Pick and pre-warm a random filler
        filler_text, filler_style, filler_deg = random.choice(FILLERS)
        filler_audio, filler_sr = await self.tts.synthesize_filler(
            filler_text, filler_style, filler_deg
        )

        # Play filler in background thread while LLM works
        filler_done = threading.Event()
        def _play_filler():
            self.player.play(filler_audio, filler_sr)
            filler_done.set()
        threading.Thread(target=_play_filler, daemon=True).start()

        # Await LLM response
        reply = await llm_task

        # Stop filler playback (whether it finished or not)
        self.player.force_stop()
        filler_done.wait(timeout=0.3)

        # Brief silence between filler and answer
        await asyncio.sleep(0.08)

        # Apply occasional false start
        voiced = maybe_false_start(reply)
        return reply, voiced

    async def run(self):
        self.initialize()
        await self.tts.warm_fillers()
        self.vad.start()
        print("\nReady.\n")

        print("=" * 54)
        print("  Speak to ask -- interrupt any time while I'm talking")
        print("  Ctrl+C to exit")
        print("=" * 54)

        greeting = (
            "Hey, I'm ready. "
            "Ask me anything about software engineering, AI, Python, "
            "whatever's on your mind. "
            "You can cut me off any time while I'm talking -- "
            "just start speaking."
        )
        print(f"\nAI: {greeting}\n")
        await self.speak(greeting, emotion="friendly")

        while True:
            try:
                while not self.speech_q.empty():
                    self.speech_q.get_nowait()

                print("Listening...")
                audio = await asyncio.get_event_loop().run_in_executor(
                    None, self.speech_q.get
                )

                self.vad.set_enabled(False)
                t0   = time.time()
                text = self.stt.transcribe(audio)
                t1   = time.time()
                self.vad.set_enabled(True)

                if not text:
                    continue

                print(f"\nYou  ({t1-t0:.1f}s): {text}")

                t2             = time.time()
                reply, voiced  = await self._turn(text)
                t3             = time.time()

                print(f"AI   ({t3-t2:.1f}s): {reply}\n")

                completed = await self.speak(voiced)
                if not completed:
                    print("[interrupted]\n")

            except KeyboardInterrupt:
                print("\nBye.")
                break
            except Exception as e:
                print(f"[error: {e}]")
                self.vad.set_enabled(True)

# ── entry ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not any(os.environ.get(k, "").strip() for k in
               ["OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]):
        print("Set an API key first:")
        print("  $env:OPENROUTER_API_KEY = 'sk-or-...'")
        sys.exit(0)
    asyncio.run(VoiceQA().run())
