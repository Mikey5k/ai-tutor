"""Quick test: SSML with edge-tts + DSP plays correctly."""
import asyncio, io, sys
import numpy as np
import sounddevice as sd
import soundfile as sf
from scipy import signal

import edge_tts

VOICE = "en-US-AriaNeural"

SSML = (
    '<speak version="1.0" '
    'xmlns="http://www.w3.org/2001/10/synthesis" '
    'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="en-US">'
    '<voice name="en-US-AriaNeural">'
    '<mstts:express-as style="friendly" styledegree="1.2">'
    '<prosody rate="-8%" pitch="+0Hz">'
    "Here's the thing about neural networks."
    '<break time="250ms"/>'
    "Think of it like a brain."
    '<break time="220ms"/>'
    "Each layer learns something <emphasis level=\"moderate\">different</emphasis>,"
    '<break time="120ms"/>'
    "and together they figure out patterns that no human explicitly programmed."
    '<break time="300ms"/>'
    "Pretty remarkable, right?"
    '</prosody>'
    '</mstts:express-as>'
    '</voice></speak>'
)

def dsp(audio, sr):
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

async def main():
    print("Synthesizing SSML with emotion style + DSP...")
    communicate = edge_tts.Communicate(SSML, voice=VOICE)
    chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])

    if not chunks:
        print("ERROR: no audio returned")
        sys.exit(1)

    audio, sr = sf.read(io.BytesIO(b"".join(chunks)), dtype="float32")
    audio = dsp(audio, sr)
    print(f"OK — {len(audio)/sr:.1f}s at {sr}Hz  Playing now...")
    sd.play(audio, sr)
    sd.wait()
    print("Done. SSML + emotion style + DSP all working.")

asyncio.run(main())
