import json
import hashlib
import os
from pathlib import Path
from typing import Optional, Any

CACHE_DIR = Path("E:/ai-tutor/data/cache")


class Cache:
    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_path(self, key: str) -> Path:
        """Get cache file path for key."""
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def exists(self, key: str) -> bool:
        """Check if key exists in cache."""
        return self.get_path(key).exists()

    def get(self, key: str) -> Optional[Any]:
        """Get cached value by key. Return None if not found."""
        path = self.get_path(key)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, value: Any):
        """Cache value by key as JSON."""
        path = self.get_path(key)
        with path.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)

    def delete(self, key: str):
        """Delete cache entry."""
        path = self.get_path(key)
        if path.exists():
            path.unlink()
