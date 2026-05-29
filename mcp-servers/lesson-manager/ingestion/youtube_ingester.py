import yt_dlp
import json
import os
import uuid
from pathlib import Path
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)

COURSES_DIR = Path("E:/ai-tutor/data/courses")


class YouTubeIngester:
    def __init__(self):
        COURSES_DIR.mkdir(parents=True, exist_ok=True)

    def get_video_info(self, url: str) -> dict:
        """Get video metadata without downloading."""
        ydl_opts = {"quiet": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                "title": info.get("title", ""),
                "duration": info.get("duration", 0),
                "description": info.get("description", ""),
            }

    def _extract_transcript(self, url: str, course_dir: Path) -> Optional[str]:
        """Try to get YouTube auto-transcript. Return text or None."""
        sub_dir = course_dir / "subs"
        sub_dir.mkdir(parents=True, exist_ok=True)

        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "writeautomaticsub": True,
            "writesubtitles": True,
            "subtitlesformat": "json3",
            "subtitleslangs": ["en"],
            "outtmpl": str(sub_dir / "transcript"),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Look for any json3 subtitle file that was written
            sub_files = list(sub_dir.glob("*.json3"))
            if not sub_files:
                # Also try vtt/srv files that yt-dlp may fall back to
                sub_files = list(sub_dir.glob("*.vtt")) + list(sub_dir.glob("*.srv*"))
                if not sub_files:
                    return None

            sub_file = sub_files[0]

            if sub_file.suffix == ".json3":
                with sub_file.open("r", encoding="utf-8") as f:
                    data = json.load(f)

                lines = []
                for event in data.get("events", []):
                    start_ms = event.get("tStartMs", 0)
                    start_s = start_ms / 1000.0
                    segs = event.get("segs", [])
                    text = "".join(s.get("utf8", "") for s in segs).strip()
                    if text:
                        lines.append(f"[{start_s:.1f}s] {text}")
                return "\n".join(lines) if lines else None
            else:
                # Plain text fallback
                return sub_file.read_text(encoding="utf-8")

        except Exception as e:
            logger.warning(f"Transcript extraction failed: {e}")
            return None

    def _download_audio(self, url: str, course_dir: Path) -> str:
        """Download audio file for Whisper transcription. Return file path."""
        ydl_opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "outtmpl": str(course_dir / "audio.%(ext)s"),
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Return path to the output mp3 file
        audio_path = course_dir / "audio.mp3"
        if not audio_path.exists():
            # yt-dlp may have kept original extension — find what was created
            candidates = list(course_dir.glob("audio.*"))
            if candidates:
                return str(candidates[0])
        return str(audio_path)

    def ingest(self, url: str, course_id: str) -> Tuple[str, str]:
        """Download and get transcript. Return (transcript_text, course_dir_path)."""
        course_dir = COURSES_DIR / course_id
        course_dir.mkdir(parents=True, exist_ok=True)

        transcript = self._extract_transcript(url, course_dir)

        if not transcript:
            logger.info("No auto-transcript found, downloading audio for Whisper")
            audio_path = self._download_audio(url, course_dir)
            # Caller is responsible for running Whisper on audio_path.
            # Return the audio path as transcript placeholder so caller knows to transcribe.
            transcript = f"__AUDIO_PATH__:{audio_path}"

        transcript_file = course_dir / "transcript.txt"
        transcript_file.write_text(transcript, encoding="utf-8")

        return transcript, str(course_dir)
