"""Founder videos: drop-folder status, uploads, YouTube links, retries."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field

from db.social_engine import founder as store
from db.social_engine import posts as post_store
from services.social_engine.context import load_store_context
from services.social_engine.errors import NotFoundError, ValidationError
from services.social_engine.founder.ingest import (
    PROCESSED_DIR, VIDEO_EXTENSIONS, canonical_youtube_url, drop_folder, ingest_file, ingest_url,
)
from services.social_engine.jobs.queue import enqueue
from services.social_engine.models import CONTENT_TYPE_LABELS

from .common import require_api_key, resolve_store_id

router = APIRouter(prefix="/api/social-engine", dependencies=[Depends(require_api_key)])

STATUS_LABELS = {
    "new": "Added", "awaiting_youtube": "Full video on its way to YouTube", "clipping": "Postiz is cutting clips",
    "done": "Clips planned", "failed": "Needs attention",
}


async def _decorate(store_id: str, video: dict) -> dict:
    out = {**video, "status_label": STATUS_LABELS.get(video["status"], video["status"]), "posts": []}
    ids = ([video["full_post_id"]] if video.get("full_post_id") else []) + list(video.get("clip_post_ids") or [])
    for post_id in ids:
        post = await post_store.get_post(store_id, post_id, with_children=False)
        if post:
            out["posts"].append({
                "id": post["id"], "status": post["status"], "title": post.get("title", ""),
                "content_type": post["content_type"],
                "content_type_label": CONTENT_TYPE_LABELS.get(post["content_type"], post["content_type"]),
                "scheduled_at": post.get("scheduled_at"),
            })
    out.pop("original_path", None)
    return out


@router.get("/founder-videos")
async def list_founder_videos(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    sctx = await load_store_context(sid)
    settings = sctx.settings.founder_videos
    folder = drop_folder(sctx.settings) if settings.enabled else Path(os.path.expanduser(settings.drop_folder))
    waiting = []
    if folder.is_dir():
        waiting = sorted(p.name for p in folder.iterdir()
                         if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in VIDEO_EXTENSIONS)
    videos = [await _decorate(sid, v) for v in await store.list_videos(sid, limit=100)]
    return {
        "store_id": sid, "videos": videos,
        "drop_folder": str(folder), "processed_folder": str(folder / PROCESSED_DIR), "waiting_files": waiting,
        "settings": settings.model_dump(),
        "extensions": sorted(VIDEO_EXTENSIONS),
    }


class UrlRequest(BaseModel):
    store_id: str = ""
    url: str = Field(..., min_length=8, max_length=300)
    note: str = Field("", max_length=500)
    product_ids: list[str] = Field(default_factory=list)


@router.post("/founder-videos/url")
async def add_youtube_url(payload: UrlRequest) -> dict[str, Any]:
    sid = await resolve_store_id(payload.store_id)
    sctx = await load_store_context(sid)
    if not canonical_youtube_url(payload.url):
        raise ValidationError("That is not a YouTube link.")
    video = await ingest_url(sctx, payload.url, note=payload.note, product_ids=payload.product_ids)
    return {"ok": True, "video": await _decorate(sid, video)}


@router.post("/founder-videos/upload")
async def upload_video(
    file: UploadFile = File(...),
    store_id: str = Form(""),
    note: str = Form(""),
    product_ids: str = Form(""),
) -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    sctx = await load_store_context(sid)
    name = Path(file.filename or "upload.mp4").name
    if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValidationError(f"{name}: only video files ({', '.join(sorted(VIDEO_EXTENSIONS))}) are accepted.")
    incoming = drop_folder(sctx.settings) / ".incoming"
    incoming.mkdir(exist_ok=True)
    tmp = incoming / f"{os.getpid()}-{name}"
    try:
        with open(tmp, "wb") as handle:
            shutil.copyfileobj(file.file, handle, length=1 << 20)
        ids = [p.strip() for p in product_ids.split(",") if p.strip()]
        video = await ingest_file(sctx, tmp, source="upload", note=note, product_ids=ids, original_name=name, move=True)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {"ok": True, "video": await _decorate(sid, video)}


@router.post("/founder-videos/scan")
async def scan_now(store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    job_id, created = await enqueue("founder.scan", {"force": True}, store_id=sid,
                                    dedupe_key=f"founder:scan:{sid}", priority=6)
    return {"ok": True, "job_id": job_id, "already_queued": not created}


@router.post("/founder-videos/{video_id}/retry")
async def retry_video(video_id: str, store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    video = await store.get_video(sid, video_id)
    if not video:
        raise NotFoundError("Founder video not found.")
    if video["status"] not in ("failed", "clipping", "awaiting_youtube"):
        raise ValidationError("Only a video that needs attention (or is still in progress) can be retried.")
    sctx = await load_store_context(sid)
    if video.get("youtube_url"):
        await store.update_video(sid, video_id, status="awaiting_youtube", clipping_id="", error="", next_check_at=0)
    elif video.get("full_post_id"):
        post = await post_store.get_post(sid, video["full_post_id"], with_children=False)
        if post and post["status"] in ("failed", "rejected", "cancelled"):
            await enqueue("post.generate", {"post_id": post["id"], "force": True}, store_id=sid,
                          dedupe_key=f"generate:{post['id']}", correlation_id=post["id"], priority=9)
            await post_store.transition_post(sid, post["id"], from_statuses=(post["status"],), to_status="planned")
        await store.update_video(sid, video_id, status="awaiting_youtube", error="", next_check_at=0)
    elif video.get("original_path") and Path(video["original_path"]).is_file():
        from services.social_engine.founder.ingest import _publish_full_video

        await _publish_full_video(sctx, video)
    else:
        raise ValidationError("Nothing to retry: the original file is gone. Drop it again.")
    await enqueue("founder.scan", {"force": True}, store_id=sid, dedupe_key=f"founder:scan:{sid}", priority=6)
    return {"ok": True, "video": await _decorate(sid, (await store.get_video(sid, video_id)) or video)}


@router.post("/founder-videos/{video_id}/delete")
async def delete_video(video_id: str, store_id: str = "") -> dict[str, Any]:
    sid = await resolve_store_id(store_id)
    video = await store.get_video(sid, video_id)
    if not video:
        raise NotFoundError("Founder video not found.")
    if video["status"] in ("awaiting_youtube", "clipping"):
        raise ValidationError("This video is still in progress; wait for it to finish or fail first.")
    await store.delete_video(sid, video_id)
    original = Path(video.get("original_path") or "")
    if original.is_file():
        original.unlink()
    return {"ok": True}
