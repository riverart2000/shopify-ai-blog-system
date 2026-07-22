"""Grok script and video generation for landing-page creative concepts."""

from __future__ import annotations

import base64
import json
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_SYSTEM_PROMPT = """You are a senior direct-response video creative director.
Create an accurate, premium short-form marketing video plan from the supplied product,
audience and creative evidence. Never invent product features, results, certifications,
prices, discounts or medical claims. The supplied creative image depicts the actual
product and must remain visually faithful.

Return only valid JSON with these keys:
duration_seconds (an integer YOU choose from 6 through 12), hook, campaign_goal,
scenes (an array covering the full duration; each scene has start_second, end_second,
visual_action, camera, on_screen_text and voiceover), audio_direction, final_cta,
video_prompt, and posting_text.

The video_prompt must be a detailed, standalone production prompt for an image-to-video
model. It must preserve the exact product, colour, shape, branding and proportions in
the reference image, specify natural motion and camera movement, and prohibit warped
products, extra parts, unreadable text and fabricated claims. Choose the shortest
duration that communicates the idea clearly; use longer durations only when the
creative genuinely needs them. Match the production specificity of a top-tier cinematic
commercial prompt: name the creative style, camera/lens look, lighting, depth of field,
colour grade, an exact second-by-second sequence covering the chosen duration, product
and environmental motion, final composition, mood, frame rate, realism and material
detail. Adapt every choice to this product and concept; do not copy irrelevant luxury,
perfume, mist or sparkle motifs. The deliverable is 480p, so do not claim a 4K output.
posting_text should be ready to publish, with a clear CTA and relevant hashtags."""

VIDEO_SCRIPT_MODEL = "grok-4.3"


class LandingPageVideoService:
    def __init__(self, settings, session) -> None:
        self.settings = settings
        self.session = session

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.grok_api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _api_error(response, operation: str) -> RuntimeError:
        try:
            body = response.json()
            detail = (body.get("error") or {}).get("message") if isinstance(body, dict) else ""
            if not detail and isinstance(body, dict):
                detail = body.get("detail") or body.get("message") or json.dumps(body)
        except Exception:  # noqa: BLE001
            detail = getattr(response, "text", "")
        return RuntimeError(
            f"xAI {operation} failed (HTTP {response.status_code}): "
            f"{str(detail or 'unknown API error')[:800]}"
        )

    def create_script(self, data: dict, concept: dict, social_text: str) -> dict:
        if not self.settings.grok_api_key:
            raise RuntimeError(
                "No xAI API key is configured for this store, so Grok cannot create the video script."
            )

        evidence = {
            "product": data.get("product") or {},
            "persona": data.get("persona") or {},
            "campaign": data.get("campaign") or {},
            "creative_concept": concept,
            "approved_image_posting_text": social_text,
        }
        payload = {
            "model": VIDEO_SCRIPT_MODEL,
            "messages": [
                {"role": "system", "content": SCRIPT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Create the strongest 6-to-12-second marketing video plan for "
                        "this exact creative. Grok must choose the duration. Evidence:\n"
                        + json.dumps(evidence, ensure_ascii=False)
                    ),
                },
            ],
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }
        response = self.session.post(
            f"{self.settings.grok_base_url.rstrip('/')}/chat/completions",
            headers=self._headers,
            json=payload,
            timeout=self.settings.grok_timeout,
        )
        if response.status_code >= 400:
            raise self._api_error(response, "video-script generation")
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
            script = json.loads(content)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Grok returned an invalid video script: {exc}") from exc

        try:
            duration = int(script.get("duration_seconds"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Grok did not choose a valid video duration.") from exc
        if not 6 <= duration <= 12:
            raise RuntimeError(
                f"Grok chose {duration} seconds, outside the required 6–12 second range. "
                "Create the script again."
            )
        if not str(script.get("video_prompt") or "").strip():
            raise RuntimeError("Grok returned a script without the required video prompt.")
        if not isinstance(script.get("scenes"), list) or not script["scenes"]:
            raise RuntimeError("Grok returned a script without a usable scene plan.")

        script["duration_seconds"] = duration
        script["posting_text"] = str(script.get("posting_text") or social_text or "").strip()
        script["created_at"] = datetime.now(timezone.utc).isoformat()
        script["model"] = VIDEO_SCRIPT_MODEL
        return script

    @staticmethod
    def _image_data_uri(image_path: Path) -> str:
        mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    def generate_video(self, script: dict, image_path: Path, output_path: Path) -> dict:
        if not self.settings.grok_api_key:
            raise RuntimeError(
                "No xAI API key is configured for this store, so Grok cannot generate the video."
            )
        if not image_path.exists():
            raise RuntimeError(f"The source creative image is missing: {image_path.name}")

        duration = int(script.get("duration_seconds") or 0)
        if not 6 <= duration <= 12:
            raise RuntimeError("Video duration must be between 6 and 12 seconds.")
        prompt = str(script.get("video_prompt") or "").strip()
        if not prompt:
            raise RuntimeError("The video script has no generation prompt.")

        payload = {
            "model": self.settings.grok_video_model,
            "prompt": prompt,
            "image": {"url": self._image_data_uri(image_path)},
            "duration": duration,
            "aspect_ratio": "9:16",
            "resolution": "480p",
        }
        response = self.session.post(
            f"{self.settings.grok_base_url.rstrip('/')}/videos/generations",
            headers=self._headers,
            json=payload,
            timeout=self.settings.grok_timeout,
        )
        if response.status_code >= 400:
            raise self._api_error(response, "video generation")
        response.raise_for_status()
        request_id = str(response.json().get("request_id") or "").strip()
        if not request_id:
            raise RuntimeError("xAI accepted the request but returned no video request ID.")

        deadline = time.monotonic() + max(600, int(self.settings.grok_timeout))
        final_body: dict[str, Any] = {}
        while time.monotonic() < deadline:
            status_response = self.session.get(
                f"{self.settings.grok_base_url.rstrip('/')}/videos/{request_id}",
                headers={"Authorization": f"Bearer {self.settings.grok_api_key}"},
                timeout=self.settings.grok_timeout,
            )
            if status_response.status_code >= 400:
                raise self._api_error(status_response, "video status check")
            status_response.raise_for_status()
            final_body = status_response.json()
            status = str(final_body.get("status") or "").lower()
            if status == "done":
                break
            if status in {"failed", "expired"}:
                reason = final_body.get("error") or final_body.get("message") or final_body
                raise RuntimeError(f"xAI video generation {status}: {str(reason)[:800]}")
            time.sleep(5)
        else:
            raise RuntimeError(
                f"xAI video generation did not finish within 10 minutes (request {request_id})."
            )

        video = final_body.get("video") or {}
        video_url = str(video.get("url") or "").strip()
        if not video_url:
            raise RuntimeError("xAI reported completion but returned no downloadable video URL.")
        download = self.session.get(video_url, timeout=max(300, self.settings.grok_timeout))
        if download.status_code >= 400:
            raise RuntimeError(
                f"The generated video could not be downloaded (HTTP {download.status_code})."
            )
        download.raise_for_status()
        if not download.content:
            raise RuntimeError("The generated video download was empty.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".mp4.tmp")
        temporary_path.write_bytes(download.content)
        temporary_path.replace(output_path)
        return {
            "request_id": request_id,
            "model": str(final_body.get("model") or self.settings.grok_video_model),
            "duration_seconds": int(video.get("duration") or duration),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "video_file": output_path.name,
            "video_version": output_path.stat().st_mtime_ns,
        }
