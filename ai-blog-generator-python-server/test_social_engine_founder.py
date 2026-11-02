"""Founder videos: file/URL intake, the YouTube → clipping → clip posts state machine."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
import pytest_asyncio

from db.social_engine import config_store
from db.social_engine import founder as store
from db.social_engine import posts as post_store
from db.social_engine import products as product_store
from services.social_engine.context import load_store_context
from services.social_engine.founder import ingest
from services.social_engine.settings import save_settings

from test_social_engine_e2e import _setup  # store, channels and products fixture helper

MASSAGE_GUN = {"product_id": "p_gun", "title": "Deep Tissue Massage Gun", "handle": "massage-gun"}
MAT = {"product_id": "p_mat", "title": "Acupressure Mat and Pillow Set", "handle": "acupressure-mat"}


def test_product_matching_from_filenames():
    products = [MASSAGE_GUN, MAT, {"product_id": "p_bowl", "title": "Singing Bowl"}, {"product_id": "p_brush", "title": "Toothbrush"}]
    assert ingest.match_products(products, "massage gun after a run.mov") == ["p_gun"]
    assert ingest.match_products(products, "why I use the acupressure mat every evening") == ["p_mat"]
    assert ingest.match_products(products, "IMG_4411.MOV") == []
    assert ingest.match_products(products, "the bowl I keep by the bed") == []            # one word of a two-word title is not enough
    assert ingest.match_products(products, "my new toothbrush routine") == ["p_brush"]   # a single-word title needs that one word
    assert ingest.match_products(products, "my morning") == []


def test_youtube_urls_and_names():
    assert ingest.canonical_youtube_url("https://youtu.be/dQw4w9WgXcQ?t=4") == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert ingest.canonical_youtube_url("https://www.youtube.com/shorts/abc123DEF_-") == "https://www.youtube.com/watch?v=abc123DEF_-"
    assert ingest.canonical_youtube_url("https://vimeo.com/123") == ""
    assert ingest.humanise_name("IMG_4411 massage_gun-after-run.MOV") == "Massage gun after run"
    assert ingest.humanise_name("") == "Founder video"


@pytest_asyncio.fixture
async def founder_store(se_db, tmp_path, monkeypatch):
    monkeypatch.setenv("SOCIAL_ENGINE_MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("SOCIAL_ENGINE_LIVE_PUBLISHING", "1")
    await _setup(se_db)
    for product in (MASSAGE_GUN, MAT):
        await product_store.upsert_product(se_db, {
            **product, "status": "ACTIVE", "available": True, "price_min": 40.0, "price_max": 40.0,
            "images": [{"url": "https://cdn.test/x.jpg"}], "featured_image_url": "https://cdn.test/x.jpg",
            "url": f"https://shop.test/products/{product['handle']}",
        })
    await config_store.upsert_channel_from_publisher(se_db, {"channel_id": "ch_youtube", "provider": "youtube", "name": "Bio Luxe Lab"})
    await save_settings(se_db, {"founder_videos": {"drop_folder": str(tmp_path / "drop"), "clips_per_video": 2}})
    return se_db


class FakeClient:
    """Stands in for PostizClient: records the clipping request, answers get_clipping."""
    started: list[dict] = []
    clipping: dict = {}
    start_error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def start_clipping(self, url, *, clips=None, fit=None, integrations=()):
        if FakeClient.start_error:
            raise FakeClient.start_error
        FakeClient.started.append({"url": url, "clips": clips, "fit": fit})
        return "clip_job_1"

    async def get_clipping(self, clipping_id):
        return FakeClient.clipping

    async def aclose(self):
        pass


@pytest.fixture
def fake_postiz(monkeypatch):
    FakeClient.started, FakeClient.clipping, FakeClient.start_error = [], {}, None
    monkeypatch.setattr(ingest, "_postiz_client", lambda sctx, correlation_id="": FakeClient())

    async def fake_download(sctx, url, *, video_id, position, default_ext):
        path = Path(os.environ["SOCIAL_ENGINE_MEDIA_ROOT"]) / f"{video_id}-{position}{default_ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake")
        return {"path": str(path), "bytes": 4, "sha256": "x" * 64, "mime": "video/mp4" if default_ext == ".mp4" else "image/jpeg"}

    monkeypatch.setattr(ingest, "_download", fake_download)
    return FakeClient


@pytest.mark.asyncio
async def test_dropped_file_becomes_full_youtube_post_then_clips(founder_store, fake_postiz, tmp_path):
    sctx = await load_store_context(founder_store)
    folder = ingest.drop_folder(sctx.settings)
    assert (folder / ingest.README_NAME).exists()
    clip = folder / "massage gun after a run.mov"
    clip.write_bytes(b"\x00" * 2048)
    old = time.time() - 600
    os.utime(clip, (old, old))

    scan = await ingest.scan_drop_folder(sctx)
    assert scan["added"] == 1 and not scan["errors"]
    assert not clip.exists() and list((folder / ingest.PROCESSED_DIR).glob("*.txt"))

    videos = await store.list_videos(founder_store)
    assert len(videos) == 1
    video = videos[0]
    assert video["status"] == "awaiting_youtube" and video["product_ids"] == ["p_gun"]
    assert video["title"] == "Massage gun after a run"
    full = await post_store.get_post(founder_store, video["full_post_id"])
    assert full["content_type"] == "founder_video" and full["options"]["channel_ids"] == ["ch_youtube"]
    assert full["options"]["premade_media"][0]["path"].endswith("original.mov")
    assert full["options"]["link_override"] == "https://shop.test/products/massage-gun"

    # Nothing happens until the YouTube target has a release URL.
    assert (await ingest.advance(sctx, force=True)) == {"waiting_for_youtube": 1}
    await post_store.replace_targets(founder_store, full["id"], [{
        "channel_id": "ch_youtube", "provider": "youtube", "caption": "x", "settings": {}, "media_ids": [],
        "format": "story", "scheduled_at": int(time.time()),
    }])
    target = (await post_store.list_targets_for_post(full["id"]))[0]
    await post_store.update_target(target["id"], status="published", release_url="https://youtu.be/abc123DEF_-")

    assert (await ingest.advance(sctx, force=True)) == {"clipping": 1}
    assert fake_postiz.started == [{"url": "https://www.youtube.com/watch?v=abc123DEF_-", "clips": 2, "fit": "crop"}]

    fake_postiz.clipping = {"status": "processing", "clips": []}
    assert (await ingest.advance(sctx, force=True)) == {"clipping": 1}
    fake_postiz.clipping = {"status": "completed", "clips": [
        {"id": "c1", "title": "Why I bought it", "content": "Thirty seconds on the trigger point.", "status": "completed",
         "mediaId": "m1", "path": "https://uploads.postiz.com/c1.mp4", "thumbnail": "https://uploads.postiz.com/c1.jpg"},
        {"id": "c2", "title": "After a run", "content": "", "status": "completed", "mediaId": "m2",
         "path": "https://uploads.postiz.com/c2.mp4"},
        {"id": "c3", "status": "failed", "path": None},
    ]}
    assert (await ingest.advance(sctx, force=True)) == {"done": 1}
    video = await store.get_video(founder_store, video["id"])
    assert video["status"] == "done" and len(video["clip_post_ids"]) == 2
    first = await post_store.get_post(founder_store, video["clip_post_ids"][0])
    assert first["content_type"] == "founder_clip" and first["title"] == "Why I bought it"
    assert first["options"]["premade_media"][0]["external_id"] == "m1"
    assert first["options"]["premade_media"][0]["thumbnail"]["mime"] == "image/jpeg"
    second = await post_store.get_post(founder_store, video["clip_post_ids"][1])
    assert second["scheduled_at"] > first["scheduled_at"]  # one clip a day

    # Running again creates nothing new.
    assert (await ingest.advance(sctx, force=True)) == {}
    assert len(await post_store.list_posts(founder_store, limit=50)) == 3


@pytest.mark.asyncio
async def test_youtube_url_goes_straight_to_clipping_and_busy_is_retried(founder_store, fake_postiz):
    from services.social_engine.errors import ExternalServiceError, ValidationError

    sctx = await load_store_context(founder_store)
    with pytest.raises(ValidationError):
        await ingest.ingest_url(sctx, "https://vimeo.com/1", note="x")
    video = await ingest.ingest_url(sctx, "https://youtu.be/dQw4w9WgXcQ", note="acupressure mat every evening")
    assert video["status"] == "awaiting_youtube" and video["product_ids"] == ["p_mat"]
    with pytest.raises(ValidationError):
        await ingest.ingest_url(sctx, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    fake_postiz.start_error = ExternalServiceError("postiz", "busy", status_code=429)
    assert (await ingest.advance(sctx, force=True)) == {"busy": 1}
    video = await store.get_video(founder_store, video["id"])
    assert video["status"] == "awaiting_youtube" and video["next_check_at"] > time.time() + 60

    fake_postiz.start_error = ExternalServiceError("postiz", "no minutes", status_code=402)
    assert (await ingest.advance(sctx, force=True)) == {"failed": 1}
    assert "minutes" in (await store.get_video(founder_store, video["id"]))["error"]


@pytest.mark.asyncio
async def test_duplicates_and_too_long_recordings(founder_store, fake_postiz, tmp_path, monkeypatch):
    from services.social_engine.errors import ValidationError

    sctx = await load_store_context(founder_store)
    src = tmp_path / "walk.mp4"
    src.write_bytes(b"\x01" * 100)
    first = await ingest.ingest_file(sctx, src, source="upload", note="evening walk")
    assert first["status"] == "awaiting_youtube"
    with pytest.raises(ValidationError, match="already added"):
        await ingest.ingest_file(sctx, src, source="upload")

    async def long_probe(path):
        return {"width": 1080, "height": 1920, "duration_ms": 50 * 60 * 1000}

    monkeypatch.setattr(ingest, "_probe", long_probe)
    other = tmp_path / "long.mov"
    other.write_bytes(b"\x02" * 100)
    row = await ingest.ingest_file(sctx, other, source="upload")
    assert row["status"] == "failed" and "40-minute" in row["error"]
