"""Storage abstraction. Barebone mode = LocalStorage (a directory).

An S3/MinIO backend can implement the same interface later; object keys are
identical in both modes so data can migrate between them. We deliberately do
NOT silently fall back from S3 to local disk (it would split data across stores).
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from .config import get_settings


class StorageBackend(Protocol):
    def put_stream(self, key: str, fh: BinaryIO) -> int: ...
    def local_path(self, key: str) -> Path: ...
    def new_path(self, key: str) -> Path: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put_stream(self, key: str, fh: BinaryIO, max_bytes: int | None = None) -> int:
        p = self.new_path(key)
        tmp = p.with_name(p.name + ".part")
        n = 0
        try:
            with open(tmp, "wb") as out:
                while chunk := fh.read(1024 * 1024):
                    n += len(chunk)
                    if max_bytes is not None and n > max_bytes:
                        raise ValueError("file exceeds maximum upload size")
                    out.write(chunk)
            tmp.replace(p)
        finally:
            tmp.unlink(missing_ok=True)
        return n

    def local_path(self, key: str) -> Path:
        return self._safe(key)

    def new_path(self, key: str) -> Path:
        """Path to write a file to directly (parent directory is created)."""
        p = self._safe(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def exists(self, key: str) -> bool:
        return self._safe(key).exists()

    def delete(self, key: str) -> None:
        p = self._safe(key)
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()


_storage: LocalStorage | None = None


def get_storage() -> LocalStorage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend != "local":
            raise RuntimeError(
                f"CLIPPIE_STORAGE_BACKEND={s.storage_backend!r} is not implemented yet; use 'local'"
            )
        _storage = LocalStorage(s.data_dir / "media")
    return _storage


def reset_storage():
    global _storage
    _storage = None
