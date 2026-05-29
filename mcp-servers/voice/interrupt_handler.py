import logging
import queue
from typing import Optional

logger = logging.getLogger(__name__)


class InterruptHandler:
    def __init__(self):
        self._pending_interrupts: queue.Queue = queue.Queue()
        self._last_transcript: Optional[str] = None

    def inject_interrupt(self, transcript: str):
        """Add transcript to interrupt queue. Called from VAD thread."""
        self._pending_interrupts.put(transcript)
        self._last_transcript = transcript
        logger.info(f"Interrupt injected: {transcript!r}")

    def get_pending_transcript(self) -> Optional[str]:
        """Get next pending interrupt transcript. Returns None if queue is empty."""
        try:
            return self._pending_interrupts.get_nowait()
        except queue.Empty:
            return None

    def get_last_transcript(self) -> Optional[str]:
        """Return most recent transcript without consuming it from the queue."""
        return self._last_transcript
