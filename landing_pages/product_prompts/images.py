"""Download product / blog images to the local output directory."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

from .config import Settings
from .models import ImageAsset
from .utils import get_logger

log = get_logger("images")

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}


class ImageDownloader:
    def __init__(self, settings: Settings, session) -> None:
        self.settings = settings
        self.session = session

    def download_many(
        self,
        urls: List[str],
        dest_dir: Path,
        prefix: str,
        limit: Optional[int] = None,
    ) -> List[ImageAsset]:
        """Download up to ``limit`` images into ``dest_dir``.

        Files are named ``<prefix>_<n><ext>`` (1-based). The first successfully
        downloaded image is tagged ``role="main"``.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        assets: List[ImageAsset] = []
        seen = set()
        for url in urls:
            if limit is not None and len(assets) >= limit:
                break
            if not url or url in seen:
                continue
            seen.add(url)
            asset = self._download_one(url, dest_dir, prefix, len(assets) + 1)
            if asset is not None:
                asset.role = "main" if not assets else "supporting"
                assets.append(asset)
        return assets

    # ------------------------------------------------------------------
    def _download_one(
        self, url: str, dest_dir: Path, prefix: str, number: int
    ) -> Optional[ImageAsset]:
        try:
            resp = self.session.get(
                url, timeout=self.settings.request_timeout, stream=True
            )
            resp.raise_for_status()
        except OSError as exc:
            log.warning("Failed to download image %s: %s", url, exc)
            return None

        ext = self._extension(url, resp.headers.get("Content-Type"))
        filename = f"{prefix}_{number}{ext}"
        path = dest_dir / filename
        try:
            with open(path, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        handle.write(chunk)
        except OSError as exc:
            log.warning("Failed to write image %s: %s", path, exc)
            return None

        log.debug("Downloaded %s -> %s", url, path)
        return ImageAsset(source_url=url, local_path=str(path))

    @staticmethod
    def _extension(url: str, content_type: Optional[str]) -> str:
        if content_type:
            base = content_type.split(";")[0].strip().lower()
            if base in _EXT_BY_CONTENT_TYPE:
                return _EXT_BY_CONTENT_TYPE[base]
            guessed = mimetypes.guess_extension(base)
            if guessed:
                return ".jpg" if guessed == ".jpe" else guessed
        path_ext = Path(urlparse(url).path).suffix.lower()
        if path_ext in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
            return ".jpg" if path_ext == ".jpeg" else path_ext
        return ".jpg"
