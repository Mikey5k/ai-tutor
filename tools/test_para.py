#!/usr/bin/env python3
"""
Paragraph quality test -- v3 (no SSML, correct duration)
Uses plain text synthesis per sentence + manual silence gaps + DSP.

Run: python E:\ai-tutor\tools\test_para.py
"""
import asyncio, io, random, re
import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy import signal
from math import gcd
import edge_tts

VOICE = "en-US-AriaNeural"
RATE  = "-8%"   # slightly slower for clarity

PARA = (
    "Here's the thing about neural networks. "
    "Think of each layer as a student who's learned to recognize one specific pattern, "
    "and together they chain those small recognitions into something remarkable. "
    "The first layer might just spot edges and colors. "
    "The next one combines those into shapes. "
    "By the time you're three or four layers deep, "
    "the network is looking at things like fluffy ears and wet nose "
    "and putting together the concept of a dog, "
    "without anyone explicitly programming what a dog is. "
    "That's the part that tends to surprise people. "
    "The intelligence wasn't written in. "
    "It emerged."
)

_SENT_SPLIT  = re.compile(r'(?<=[.!?])\s+')
_SENT_ENDER  = re.compile(r'([.!?])\s*$')

def dsp(audio, sr):
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype("float64")
    audio = signal.sosfilt(signal.butter(2, 80, "hp", fs=sr, output="sos"), audio)
    audio += signal.sosfilt(signal.butter(2, [2000,5000], "bandpass", fs=sr, output="sos"), audio) * 0.13
    audio += signal.sosfilt(signal.butter(2, [300,800], "bandpass", fs=sr, output="sos"), audio) * 0.06
    thr = 0.58
    mask = np.abs(audio) > thr
    audio[mask] = np.sign(audio[mask]) * (thr + (np.abs(audio[mask]) - thr) / 3.2)
    n7, n13 = int(sr*0.007), int(sr*0.013)
    r1 = np.zeros_like(audio); r1[n7:]  = audio[:-n7]  * 0.085
    r2 = np.zeros_like(audio); r2[n13:] = audio[:-n13] * 0.038
    audio = audio + r1 + r2
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio / peak * 0.87
    return audio.astype("float32")

def find_wasapi_out():
    try:
        hostapis = sd.query_hostapis()
        wh = next((i for i,h in enumerate(hostapis) if "WASAPI" in h["name"]), None)
        if wh is None: return None, None
        devices = sd.query_devices()
        for i, d in enumerate(devices):
            if d["hostapi"] == wh and d["max_output_channels"] > 0:
                return i, int(d["default_samplerate"])
    except Exception:
        pass
    return None, 48000

async def synth_plain(text, rate):
    communicate = edge_tts.Communicate(text.strip(), voice=VOICE, rate=rate)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    if not chunks:
        return np.zeros(100, dtype="float32"), 24000
    return sf.read(io.BytesIO(b"".join(chunks)), dtype="float32")

async def main():
    print()
    print("Paragraph quality test -- v3")
    print(f"  Rate: {RATE}  |  Sentence-level synthesis + manual silence")
    print(f"  DSP: high-pass + presence + warmth + compression + room")
    print()
    print("Text:")
    print(PARA)
    print()

    sentences = [s.strip() for s in _SENT_SPLIT.split(PARA) if s.strip()]
    print(f"Synthesizing {len(sentences)} sentences...")

    parts = []
    sr    = None
    for i, sent in enumerate(sentences):
        print(f"  [{i+1}/{len(sentences)}] {sent[:50]}...")
        frag, frag_sr = await synth_plain(sent, RATE)
        if sr is None:
            sr = frag_sr
        parts.append(frag)

        if i < len(sentences) - 1:
            m = _SENT_ENDER.search(sent)
            if m and m.group(1) == "?":
                gap_ms = random.randint(290, 370)
            elif m and m.group(1) == "!":
                gap_ms = random.randint(240, 300)
            else:
                gap_ms = random.randint(200, 260)
            parts.append(np.zeros(int(frag_sr * gap_ms / 1000), dtype="float32"))

    combined = np.concatenate(parts)
    combined = dsp(combined, sr)

    # Resample to WASAPI native rate
    out_dev, native_sr = find_wasapi_out()
    if sr != native_sr:
        g = gcd(native_sr, sr)
        combined = signal.resample_poly(combined.astype("float64"),
                                        native_sr // g, sr // g).astype("float32")
        sr = native_sr

    duration = len(combined) / sr
    print(f"\nDone. {duration:.1f}s total audio at {sr}Hz")
    if out_dev is not None:
        dev_name = sd.query_devices(out_dev)["name"]
        print(f"Playing via WASAPI: {dev_name}")
    print()

    sd.play(combined, sr, device=out_dev)
    sd.wait()

    print("Complete. If quality is good, run the interview:")
    print("  python E:\\ai-tutor\\tools\\interview_test.py")
    print()

asyncio.run(main())
