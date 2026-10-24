"""Classic product videos (Outfy parity) rendered locally with Pillow + ffmpeg.

Structure: intro slide (hero image + title) → 1–4 product shots with Ken Burns
motion (zoompan) and a caption overlay → price/sticker slide → end card
(CTA, logo, website), joined with crossfades (xfade). 30 fps H.264 yuv420p,
`+faststart`, AAC audio (brand music trimmed and faded, else silence).
The multi-product version shows a product grid intro and one shot per product.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..errors import ConfigurationError, SocialEngineError, ValidationError
from .base import RenderedMedia
from .images import (
    ImageFetcher, encode_jpeg, encode_png, fit_contain, fit_cover_soft, has_plain_background, plain_background_color,
    trim_white,
)
from .layout import ProductInfo, first_sentence, image_url_for, render_layers, render_spec
from .prepare import Prepared, fetch_images, spec_urls
from .storage import finalize_file, media_path, save_media_bytes, temp_path_for
from .templates import FRAMES, video_slide_spec

logger = logging.getLogger("ai_blog_server.social_engine.creatives")

FPS = 30
CROSSFADE = 0.5
MIN_SECONDS, MAX_SECONDS = 8.0, 15.0
FFMPEG_TIMEOUT = 240.0
FFPROBE_TIMEOUT = 30.0
MOTION_SCALE = 2          # Ken Burns source is rendered at 2× to keep zoompan smooth
_SEARCH_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin")
_SEMAPHORES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def find_binary(name: str) -> str:
    """Locate ffmpeg/ffprobe: $SOCIAL_ENGINE_FFMPEG / $SOCIAL_ENGINE_FFPROBE, PATH, Homebrew."""
    override = os.environ.get(f"SOCIAL_ENGINE_{name.upper()}", "").strip()
    if override:
        if os.path.isfile(override) and os.access(override, os.X_OK):
            return override
        raise ConfigurationError(f"SOCIAL_ENGINE_{name.upper()} points to '{override}', which is not an executable file.")
    found = shutil.which(name)
    if found:
        return found
    for directory in _SEARCH_DIRS:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise ConfigurationError(
        f"{name} was not found. Install it (brew install ffmpeg) or set SOCIAL_ENGINE_{name.upper()} to its path."
    )


def ffmpeg_available() -> bool:
    try:
        find_binary("ffmpeg")
        find_binary("ffprobe")
    except ConfigurationError:
        return False
    return True


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(int(os.environ.get("SOCIAL_ENGINE_VIDEO_CONCURRENCY", "2") or 2))
        _SEMAPHORES[loop] = sem
    return sem


def stderr_tail(data: bytes, *, lines: int = 25, limit: int = 2500) -> str:
    text = data.decode("utf-8", errors="replace").strip()
    tail = "\n".join(text.splitlines()[-lines:])
    return tail[-limit:]


async def run_tool(args: list[str], *, timeout: float, tool: str = "ffmpeg") -> bytes:
    """Run ffmpeg/ffprobe (no shell). Returns stdout; raises with the exact stderr tail."""
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        _, stderr = await proc.communicate()
        raise SocialEngineError(
            f"{tool} timed out after {timeout:.0f}s: {stderr_tail(stderr or b'', lines=8)}",
            details={"tool": tool}, retryable=True,
        ) from exc
    if proc.returncode != 0:
        tail = stderr_tail(stderr or b"")
        raise SocialEngineError(
            f"{tool} failed (exit {proc.returncode}): {tail}",
            details={"tool": tool, "exit_code": proc.returncode, "stderr_tail": tail},
        )
    return stdout or b""


# ---------------------------------------------------------------------------
# Slide planning
# ---------------------------------------------------------------------------

@dataclass
class VideoSlide:
    kind: str                       # still | motion
    duration: float
    image: Path
    overlay: Path | None = None
    motion: str = "zoom_in"         # zoom_in | zoom_out | pan_right | pan_left


@dataclass
class VideoPlan:
    fmt: str
    width: int
    height: int
    slides: list[VideoSlide] = field(default_factory=list)
    thumbnail: bytes = b""

    @property
    def duration(self) -> float:
        total = sum(s.duration for s in self.slides) - CROSSFADE * max(0, len(self.slides) - 1)
        return round(total, 3)


def _scale_durations(durations: list[float], target: float | None) -> list[float]:
    n = len(durations)
    total = sum(durations) - CROSSFADE * max(0, n - 1)
    goal = target if target else total
    goal = max(MIN_SECONDS, min(MAX_SECONDS, goal))
    if abs(goal - total) < 0.01:
        return durations
    factor = (goal + CROSSFADE * max(0, n - 1)) / sum(durations)
    return [round(d * factor, 3) for d in durations]


def _motion_canvas(img: Image.Image, size: tuple[int, int], background: tuple[int, int, int]) -> Image.Image:
    """Full-frame photo for Ken Burns: packshots float on their own backdrop, photos fill the frame."""
    w, h = size
    if has_plain_background(img):
        fill = plain_background_color(img)
        canvas = Image.new("RGB", (w, h), fill)
        product = fit_contain(trim_white(img), (int(w * 0.74), int(h * 0.56)))
        canvas.paste(product, ((w - product.width) // 2, int(h * 0.42 - product.height / 2)), product)
        return canvas
    flat = Image.new("RGBA", img.size, (*background, 255))
    flat.alpha_composite(img)
    # Keep the subject in the upper part of the frame: the caption sits on the lower third.
    return fit_cover_soft(flat, (w, h), max_crop=0.3, anchor=(0.5, 0.12), max_height=0.8).convert("RGB")


def _captions(prepared: Prepared, product: ProductInfo, index: int) -> tuple[str, str]:
    texts = prepared.texts
    if index == 0:
        return (texts.get("eyebrow") or (product.collections[0] if product.collections else "")), product.title
    if index == 1:
        line = texts.get("subheadline") or first_sentence(product.description, 90)
        return "", line
    if index == 2 and texts.get("promo_text"):
        return "", texts["promo_text"]
    return "", ""


def _render_still(prepared: Prepared, slide: str, fmt: str, path: Path, *, texts: dict[str, str] | None = None,
                  products: list[ProductInfo] | None = None) -> bytes:
    ctx = prepared.context(fmt, texts=texts, products=products)
    canvas = render_spec(ctx, video_slide_spec(slide, fmt)).convert("RGB")
    data = encode_jpeg(canvas, quality=94)
    path.write_bytes(data)
    return data


def _render_motion(prepared: Prepared, fmt: str, img: Image.Image, workdir: Path, index: int,
                   texts: dict[str, str], products: list[ProductInfo]) -> tuple[Path, Path]:
    frame = FRAMES[fmt]
    big = (frame.W * MOTION_SCALE, frame.H * MOTION_SCALE)
    canvas = _motion_canvas(img, big, prepared.brand.colors["background"])
    image_path = workdir / f"motion_{index}.jpg"
    image_path.write_bytes(encode_jpeg(canvas, quality=93))
    ctx = prepared.context(fmt, texts=texts, products=products)
    ctx.background = prepared.brand.colors["secondary"]
    layers = [layer for layer in video_slide_spec("motion_overlay", fmt)["layers"]
              if not (layer.get("type") == "scrim" and not texts.get("caption"))]
    overlay = render_layers(ctx, layers, transparent=True)
    overlay_path = workdir / f"overlay_{index}.png"
    overlay_path.write_bytes(encode_png(overlay))
    return image_path, overlay_path


def _image_list(prepared: Prepared, multi: bool) -> list[tuple[ProductInfo, str]]:
    offset = int(prepared.request.image_offset or 0)
    limit = int(prepared.options.get("max_images") or prepared.template.video.get("max_images", 4))
    limit = max(1, min(4, limit))
    if multi:
        return [(p, url) for p in prepared.products[:limit]
                if (url := image_url_for(p, 0, offset))]
    product = prepared.products[0]
    count = min(limit, len(product.image_urls))
    return [(product, url) for i in range(count) if (url := image_url_for(product, i, offset))]


def _plan_sync(prepared: Prepared, fmt: str, workdir: Path, shots: list[tuple[ProductInfo, str]], multi: bool) -> VideoPlan:
    frame = FRAMES[fmt]
    plan = VideoPlan(fmt, frame.W, frame.H)
    slides: list[VideoSlide] = []
    intro = workdir / "intro.jpg"
    if multi:
        plan.thumbnail = _render_still(prepared, "multi_intro", fmt, intro)
    else:
        plan.thumbnail = _render_still(prepared, "intro", fmt, intro)
    slides.append(VideoSlide("still", 2.6, intro))
    motions = ("zoom_in", "pan_right", "zoom_out", "pan_left")
    for index, (product, url) in enumerate(shots):
        img = prepared.images[url]
        if multi:
            kicker, caption = product.price if prepared.options.get("show_price", True) else "", product.title
        else:
            kicker, caption = _captions(prepared, product, index)
        texts = {"caption": caption, "caption_kicker": kicker if caption else ""}
        image_path, overlay_path = _render_motion(prepared, fmt, img, workdir, index, texts, [product])
        slides.append(VideoSlide("motion", 2.4, image_path, overlay_path, motions[index % len(motions)]))
    if not multi:
        price_path = workdir / "price.jpg"
        _render_still(prepared, "price", fmt, price_path)
        slides.append(VideoSlide("still", 2.8, price_path))
    end_path = workdir / "end.jpg"
    _render_still(prepared, "end", fmt, end_path)
    slides.append(VideoSlide("still", 2.6, end_path))
    target = prepared.options.get("duration_seconds")
    durations = _scale_durations([s.duration for s in slides], float(target) if target else None)
    for slide, duration in zip(slides, durations):
        slide.duration = duration
    plan.slides = slides
    return plan


# ---------------------------------------------------------------------------
# ffmpeg composition
# ---------------------------------------------------------------------------

def _zoompan(slide: VideoSlide, width: int, height: int) -> str:
    frames = max(2, int(round(slide.duration * FPS)))
    n = frames - 1
    zoom = 0.12
    if slide.motion == "zoom_out":
        z = f"{1 + zoom}-{zoom}*on/{n}"
        x, y = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    elif slide.motion in {"pan_right", "pan_left"}:
        z = f"{1 + zoom * 0.8}"
        progress = f"on/{n}" if slide.motion == "pan_right" else f"(1-on/{n})"
        x, y = f"(iw-iw/zoom)*{progress}", "(ih-ih/zoom)/2"
    else:
        z = f"1+{zoom}*on/{n}"
        x, y = "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={FPS}"


def build_ffmpeg_args(ffmpeg: str, plan: VideoPlan, output: Path, music: str = "") -> list[str]:
    args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    filters: list[str] = []
    index = 0
    labels: list[str] = []
    for number, slide in enumerate(plan.slides):
        if slide.kind == "motion":
            args += ["-i", str(slide.image)]
            image_input = index
            index += 1
            args += ["-i", str(slide.overlay)]
            overlay_input = index
            index += 1
            filters.append(
                f"[{image_input}:v]{_zoompan(slide, plan.width, plan.height)},setsar=1,format=yuv420p[m{number}];"
                f"[m{number}][{overlay_input}:v]overlay=0:0:format=auto:eof_action=repeat,"
                f"trim=duration={slide.duration:.3f},fps={FPS},format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[s{number}]"
            )
        else:
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{slide.duration:.3f}", "-i", str(slide.image)]
            filters.append(
                f"[{index}:v]scale={plan.width}:{plan.height},setsar=1,fps={FPS},format=yuv420p,settb=AVTB,"
                f"setpts=PTS-STARTPTS[s{number}]"
            )
            index += 1
        labels.append(f"s{number}")
    total = plan.duration
    current = labels[0]
    elapsed = plan.slides[0].duration
    for number in range(1, len(labels)):
        offset = elapsed - CROSSFADE * number
        out = f"x{number}"
        filters.append(f"[{current}][{labels[number]}]xfade=transition=fade:duration={CROSSFADE}:offset={offset:.3f}[{out}]")
        current = out
        elapsed += plan.slides[number].duration
    filters.append(
        f"[{current}]scale=out_range=tv:out_color_matrix=bt709,format=yuv420p,"
        f"setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709[vout]"
    )
    if music:
        args += ["-stream_loop", "-1", "-i", music]
        fade = min(1.5, total / 4)
        filters.append(
            f"[{index}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aformat=sample_rates=44100:channel_layouts=stereo,"
            f"afade=t=in:st=0:d=0.3,afade=t=out:st={total - fade:.3f}:d={fade:.3f}[aout]"
        )
    else:
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        filters.append(f"[{index}:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS[aout]")
    args += [
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", os.environ.get("SOCIAL_ENGINE_X264_PRESET", "medium"), "-crf", "20",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(FPS), "-color_range", "tv",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-t", f"{total:.3f}", "-movflags", "+faststart", "-f", "mp4", str(output),
    ]
    return args


async def probe_video(path: str | Path) -> dict:
    ffprobe = find_binary("ffprobe")
    raw = await run_tool(
        [ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate",
         "-show_entries", "format=duration", "-of", "json", str(path)],
        timeout=FFPROBE_TIMEOUT, tool="ffprobe",
    )
    data = json.loads(raw.decode("utf-8") or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {
        "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
        "codec": video.get("codec_name", ""), "pix_fmt": video.get("pix_fmt", ""),
        "audio_codec": audio.get("codec_name", ""),
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
    }


def _validate_probe(info: dict, plan: VideoPlan) -> None:
    problems = []
    if (info["width"], info["height"]) != (plan.width, plan.height):
        problems.append(f"size {info['width']}x{info['height']} != {plan.width}x{plan.height}")
    if info["codec"] != "h264" or info["pix_fmt"] != "yuv420p":
        problems.append(f"codec {info['codec']}/{info['pix_fmt']} != h264/yuv420p")
    if abs(info["duration"] - plan.duration) > 0.4:
        problems.append(f"duration {info['duration']:.2f}s != {plan.duration:.2f}s")
    if not info["audio_codec"]:
        problems.append("no audio track")
    if problems:
        raise SocialEngineError(f"Rendered video failed validation: {'; '.join(problems)}", details=info)


def _music_path(prepared: Prepared) -> str:
    path = str(prepared.options.get("music_path") or prepared.request.brand.music_path or "").strip()
    if not path:
        return ""
    expanded = os.path.expanduser(path)
    if not os.path.isfile(expanded):
        raise ValidationError(f"Background music file not found: {path}")
    return expanded


async def _prepare_assets(prepared: Prepared, formats: list[str], fetcher: ImageFetcher) -> tuple[bool, list[tuple[ProductInfo, str]]]:
    multi = prepared.template.video.get("style") == "multi"
    if not prepared.products:
        raise ValidationError(f"Template '{prepared.template.key}' needs at least 1 product(s); got 0.")
    shots = _image_list(prepared, multi)
    urls = [url for _, url in shots]
    for fmt in formats:
        urls += spec_urls(prepared, video_slide_spec("multi_intro" if multi else "intro", fmt))
        if not multi:
            urls += spec_urls(prepared, video_slide_spec("price", fmt))
    await fetch_images(prepared, fetcher, urls)
    return multi, shots


async def render_video(prepared: Prepared, formats: list[str], fetcher: ImageFetcher) -> list[RenderedMedia]:
    """Render one classic video (+ JPEG thumbnail) per format."""
    ffmpeg = find_binary("ffmpeg")
    find_binary("ffprobe")
    music = _music_path(prepared)
    multi, shots = await _prepare_assets(prepared, formats, fetcher)
    request = prepared.request
    source = f"ffmpeg:{prepared.template.key}"
    media: list[RenderedMedia] = []
    for fmt in formats:
        workdir = Path(tempfile.mkdtemp(prefix="se-video-"))
        try:
            plan = await asyncio.to_thread(_plan_sync, prepared, fmt, workdir, shots, multi)
            final = media_path(request.store_id, request.post_id, fmt, 0, "mp4")
            tmp = temp_path_for(final)
            args = build_ffmpeg_args(ffmpeg, plan, tmp, music)
            timeout = float(prepared.options.get("ffmpeg_timeout") or FFMPEG_TIMEOUT)
            try:
                async with _semaphore():
                    await run_tool(args, timeout=timeout)
                info = await probe_video(tmp)
                _validate_probe(info, plan)
                stored = await asyncio.to_thread(finalize_file, tmp, final)
            finally:
                if tmp.exists():
                    tmp.unlink()
            thumb = await asyncio.to_thread(
                save_media_bytes, request.store_id, request.post_id, fmt, 0, "jpg", plan.thumbnail,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        media.append(RenderedMedia(
            path=stored.path, kind="video", format=fmt, mime="video/mp4", width=plan.width, height=plan.height,
            bytes=stored.bytes, sha256=stored.sha256, duration_ms=int(round(info["duration"] * 1000)),
            role="primary", position=0, source=source,
        ))
        media.append(RenderedMedia(
            path=thumb.path, kind="image", format=fmt, mime="image/jpeg", width=plan.width, height=plan.height,
            bytes=thumb.bytes, sha256=thumb.sha256, role="thumbnail", position=0, source=source,
        ))
        logger.info("Rendered %s video (%.1fs, %d slides) for post %s", fmt, info["duration"], len(plan.slides),
                    request.post_id)
    return media


async def render_thumbnail(prepared: Prepared, fmt: str, fetcher: ImageFetcher) -> RenderedMedia:
    """Only the intro slide (used for template previews of video templates)."""
    multi, _ = await _prepare_assets(prepared, [fmt], fetcher)
    slide = "multi_intro" if multi else "intro"

    def _render() -> bytes:
        ctx = prepared.context(fmt)
        return encode_jpeg(render_spec(ctx, video_slide_spec(slide, fmt)).convert("RGB"), quality=90)

    data = await asyncio.to_thread(_render)
    request = prepared.request
    stored = await asyncio.to_thread(save_media_bytes, request.store_id, request.post_id, fmt, 0, "jpg", data)
    frame = FRAMES[fmt]
    return RenderedMedia(
        path=stored.path, kind="image", format=fmt, mime="image/jpeg", width=frame.W, height=frame.H,
        bytes=stored.bytes, sha256=stored.sha256, role="thumbnail", position=0,
        source=f"ffmpeg:{prepared.template.key}",
    )
