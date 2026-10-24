"""Local media storage for rendered creatives.

Layout::

    {root}/{store_id}/{YYYY}/{MM}/{post_id}/{format}_{position}_{shortuuid}.{ext}
    {root}/_cache/{aa}/{sha256-of-url}            downloaded product images

`root` is `$SOCIAL_ENGINE_MEDIA_ROOT` or `<server dir>/data/social_engine/media`.
Every write is atomic (temp file in the same directory + `os.replace`), so a
crash never leaves a half-written file under a final name.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ..errors import NotFoundError, ValidationError

logger = logging.getLogger("ai_blog_server.social_engine.creatives")

MEDIA_ROOT_ENV = "SOCIAL_ENGINE_MEDIA_ROOT"
CACHE_DIR_NAME = "_cache"
_SERVER_DIR = Path(__file__).resolve().parents[3]
_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class StoredFile:
    path: str
    bytes: int
    sha256: str


def media_root() -> Path:
    """Absolute media root (created on first use)."""
    configured = os.environ.get(MEDIA_ROOT_ENV, "").strip()
    root = Path(configured).expanduser() if configured else _SERVER_DIR / "data" / "social_engine" / "media"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_dir() -> Path:
    path = media_root() / CACHE_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_segment(value: str, *, fallback: str = "_") -> str:
    """A single path segment that cannot escape its parent directory."""
    cleaned = _SAFE_SEGMENT.sub("-", str(value or "")).strip(".-")
    return cleaned[:80] or fallback


def short_uuid() -> str:
    return uuid.uuid4().hex[:10]


def media_path(
    store_id: str,
    post_id: str,
    fmt: str,
    position: int,
    ext: str,
    *,
    when: datetime | None = None,
) -> Path:
    """Final path for a new media file (parent directories are created)."""
    moment = when or datetime.now(timezone.utc)
    directory = (
        media_root() / safe_segment(store_id) / f"{moment.year:04d}" / f"{moment.month:02d}"
        / safe_segment(post_id, fallback="_unassigned")
    )
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{safe_segment(fmt, fallback='media')}_{int(position)}_{short_uuid()}.{safe_segment(ext.lstrip('.'), fallback='bin')}"
    return directory / name


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: str | Path) -> tuple[str, int]:
    """(sha256 hex, size in bytes) of a file on disk."""
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def temp_path_for(final: Path) -> Path:
    """A unique temporary path next to `final` (same filesystem → atomic replace)."""
    final.parent.mkdir(parents=True, exist_ok=True)
    return final.with_name(f".{final.stem}.{uuid.uuid4().hex[:8]}.tmp{final.suffix}")


def write_bytes_atomic(path: str | Path, data: bytes) -> StoredFile:
    final = Path(path)
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{final.name}.", suffix=".tmp", dir=str(final.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, final)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return StoredFile(path=str(final), bytes=len(data), sha256=sha256_bytes(data))


def save_media_bytes(
    store_id: str, post_id: str, fmt: str, position: int, ext: str, data: bytes,
) -> StoredFile:
    return write_bytes_atomic(media_path(store_id, post_id, fmt, position, ext), data)


def finalize_file(tmp: str | Path, final: str | Path) -> StoredFile:
    """Atomically move a fully written temp file (e.g. ffmpeg output) into place."""
    os.replace(tmp, final)
    sha, size = file_digest(final)
    return StoredFile(path=str(final), bytes=size, sha256=sha)


def _inside_root(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except ValueError:
        return False


def resolve_media_path(path: str | Path, *, must_exist: bool = True) -> Path:
    """Resolve a stored media path (absolute, or relative to the root) safely.

    Raises ValidationError for anything outside the media root (including
    `..` tricks and symlinks pointing elsewhere) and NotFoundError when the
    file does not exist.
    """
    raw = str(path or "").strip()
    if not raw or "\x00" in raw:
        raise ValidationError("Media path is empty or invalid.")
    root = media_root()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if resolved == root or not _inside_root(resolved, root):
        raise ValidationError(f"Media path is outside the media root: {raw}")
    if must_exist and not resolved.is_file():
        raise NotFoundError(f"Media file not found: {raw}")
    return resolved


def _remove_empty_parents(path: Path, root: Path) -> None:
    parent = path.parent
    while parent != root and _inside_root(parent, root):
        try:
            parent.rmdir()
        except OSError:
            return
        parent = parent.parent


def _delete_files_sync(paths: Iterable[str]) -> int:
    root = media_root()
    deleted = 0
    for item in paths:
        if not item:
            continue
        try:
            resolved = resolve_media_path(item, must_exist=False)
        except ValidationError:
            logger.warning("Refusing to delete a file outside the media root: %s", item)
            continue
        try:
            resolved.unlink()
            deleted += 1
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not delete media file %s: %s", resolved, exc)
            continue
        _remove_empty_parents(resolved, root)
    return deleted


async def delete_files(paths: Iterable[str]) -> int:
    """Delete media files (only inside the media root). Returns the number removed."""
    return await asyncio.to_thread(_delete_files_sync, list(paths))


def _prune_orphans_sync(
    known: set[str], store_id: str, min_age_seconds: int, now_ts: float, dry_run: bool,
) -> list[str]:
    root = media_root()
    base = root / safe_segment(store_id) if store_id else root
    if not base.is_dir():
        return []
    removed: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        if current == root:
            dirnames[:] = [name for name in dirnames if name != CACHE_DIR_NAME]
        for name in filenames:
            path = current / name
            if str(path) in known:
                continue
            try:
                age = now_ts - path.stat().st_mtime
            except OSError:
                continue
            if age < min_age_seconds:
                continue
            removed.append(str(path))
            if not dry_run:
                try:
                    path.unlink()
                except OSError as exc:
                    logger.warning("Could not prune media file %s: %s", path, exc)
                    removed.pop()
                    continue
                _remove_empty_parents(path, root)
    return removed


async def prune_orphans(
    known_paths: Iterable[str],
    *,
    store_id: str = "",
    min_age_seconds: int = 24 * 3600,
    dry_run: bool = False,
) -> list[str]:
    """Delete media files that no database row references.

    `known_paths` are the paths still referenced (e.g. every `social_media_assets.path`).
    Files younger than `min_age_seconds` are kept so an in-flight render is never
    removed before its row is written. The image cache is never touched (see
    `prune_cache`). Returns the removed (or, with `dry_run`, removable) paths.
    """
    known: set[str] = set()
    for item in known_paths:
        if not item:
            continue
        try:
            known.add(str(resolve_media_path(item, must_exist=False)))
        except ValidationError:
            continue
    return await asyncio.to_thread(
        _prune_orphans_sync, known, store_id, int(min_age_seconds), time.time(), dry_run,
    )


def _prune_cache_sync(max_age_seconds: int, now_ts: float) -> int:
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(cache_dir()):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                if now_ts - path.stat().st_mtime >= max_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


async def prune_cache(*, max_age_seconds: int = 14 * 24 * 3600) -> int:
    """Remove cached downloads older than `max_age_seconds`."""
    return await asyncio.to_thread(_prune_cache_sync, int(max_age_seconds), time.time())
