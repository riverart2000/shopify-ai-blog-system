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
Create an accurate, authentic UGC short-form marketing video plan from the supplied
product, audience and creative evidence. The fictional ideal-customer persona in the
approved creative image is the on-camera spokesperson. Never invent product features,
results, certifications, prices, discounts or medical claims. The supplied creative
image depicts the actual product and spokesperson and both must remain visually
faithful.

Return only valid JSON with these keys:
duration_seconds (an integer YOU choose from 6 through 12), hook, campaign_goal,
scenes (an array covering the full duration; each scene has start_second, end_second,
visual_action, camera, on_screen_text and speech), spoken_script, audio_direction,
final_cta, video_prompt, and posting_text.

The video_prompt must be a detailed, standalone production prompt for an image-to-video
model. It must create a realistic vertical 9:16 UGC/TikTok/Reels video in which the
spokesperson speaks directly to the viewer in a natural first-person, conversational
tone. Use realistic speech audio and precise lip-sync: mouth shapes must match every
spoken word, with natural blinking, micro-expressions, head movement, breathing and
small hand gestures. Keep the face and mouth visible while speaking. Do not use an
off-camera narrator or silent montage.

spoken_script is the exact dialogue the spokesperson says aloud. It must be a single
natural conversational passage, grounded in the supplied evidence, with no quotation
marks around the stored value. Keep it short enough for relaxed, intelligible delivery
at roughly 2 to 2.5 words per second within the chosen duration. Include the exact same
dialogue verbatim inside video_prompt under a clearly labelled "Script (speak exactly)"
section, surrounded by quotation marks. Every scene's speech must be taken verbatim
from spoken_script and the scene timings must cover the full chosen duration.

Preserve the exact product, colour, shape, branding and proportions in the reference
image and preserve the approved fictional spokesperson's appearance. Specify natural
handheld phone movement, casual framing, natural lighting and an authentic relatable
setting. Prohibit warped products, extra fingers or limbs, identity drift, unreadable
text, fabricated claims, robotic delivery and lip-sync mismatch. Choose the shortest
duration that communicates the idea clearly; use longer durations only when genuinely
needed. The deliverable is 480p, so do not claim a 4K output. posting_text should be
ready to publish, with a clear CTA and relevant hashtags."""

VIDEO_SCRIPT_MODEL = "grok-4.3"
MAX_SPEECH_WORDS_PER_SECOND = 2.6


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
        spoken_script = self._spoken_script_from_response(script, duration)

        script["duration_seconds"] = duration
        script["spoken_script"] = spoken_script
        script["speech_warning"] = self._speech_fit_warning(
            spoken_script, duration
        )
        script["video_prompt"] = self._ugc_video_prompt(
            str(script.get("video_prompt") or "").strip(),
            spoken_script,
            duration,
        )
        script["posting_text"] = str(script.get("posting_text") or social_text or "").strip()
        script["created_at"] = datetime.now(timezone.utc).isoformat()
        script["model"] = VIDEO_SCRIPT_MODEL
        return script

    @staticmethod
    def _validate_spoken_script(value: Any, duration: int) -> str:
        spoken_script = str(value or "").strip()
        if (
            len(spoken_script) >= 2
            and spoken_script[0] == spoken_script[-1]
            and spoken_script[0] in {'"', "'"}
        ):
            spoken_script = spoken_script[1:-1].strip()
        if not spoken_script:
            raise RuntimeError(
                "Grok returned no spoken_script. UGC video generation requires the "
                "exact words the on-camera spokesperson will say for lip-sync, and "
                "no recoverable scene speech was present. No retry was attempted."
            )
        return spoken_script

    @classmethod
    def _spoken_script_from_response(cls, script: dict, duration: int) -> str:
        """Preserve paid results when Grok places dialogue in the scene objects."""
        spoken_script = str(script.get("spoken_script") or "").strip()
        if not spoken_script:
            pieces: list[str] = []
            for scene in script.get("scenes") or []:
                if not isinstance(scene, dict):
                    continue
                piece = str(scene.get("speech") or scene.get("voiceover") or "").strip()
                if piece and (not pieces or pieces[-1] != piece):
                    pieces.append(piece)
            spoken_script = " ".join(pieces)
        return cls._validate_spoken_script(spoken_script, duration)

    @staticmethod
    def _speech_fit_warning(spoken_script: str, duration: int) -> str:
        word_count = len(spoken_script.split())
        maximum_words = int(duration * MAX_SPEECH_WORDS_PER_SECOND)
        if word_count > maximum_words:
            return (
                f"Grok wrote {word_count} spoken words for a {duration}-second video; "
                f"approximately {maximum_words} words is safer for clear UGC lip-sync. "
                "The paid script was preserved. Shorten the editable spoken script "
                "before generating the video, or accept a faster delivery."
            )
        return ""

    @staticmethod
    def _ugc_video_prompt(base_prompt: str, spoken_script: str, duration: int) -> str:
        """Build the final renderer prompt with immutable lip-sync instructions."""
        marker = "MANDATORY UGC SPEECH AND LIP-SYNC INSTRUCTIONS"
        # create_script stores the composed prompt for review in the admin UI.
        # Recompose at render time so edited dialogue is injected once, not appended
        # beneath stale or duplicated speech instructions.
        base_prompt = base_prompt.split(marker, 1)[0].rstrip()
        return (
            f"{base_prompt}\n\n"
            f"{marker} (these override any "
            "conflicting audio or dialogue instruction above):\n"
            "- Format: authentic vertical 9:16 UGC / TikTok / Instagram Reels video.\n"
            "- The fictional spokesperson shown in the reference image speaks directly "
            "to camera; preserve their approved appearance and the exact product.\n"
            "- Generate natural audible speech, not captions-only and not an off-camera "
            "voiceover.\n"
            "- Keep the speaker's face and mouth clearly visible while speaking.\n"
            "- Synchronize every mouth movement precisely to every spoken phoneme, with "
            "natural blinking, breathing, micro-expressions, head motion and gestures.\n"
            "- Natural conversational delivery; no robotic cadence, frozen face, mouth "
            "drift, identity drift or mismatched lip movement.\n"
            f"- Exact duration: {duration} seconds.\n"
            "Script (speak exactly, with no added or omitted words):\n"
            f"\"{spoken_script}\""
        )

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
        base_prompt = str(script.get("video_prompt") or "").strip()
        if not base_prompt:
            raise RuntimeError("The video script has no generation prompt.")
        spoken_script = self._validate_spoken_script(
            script.get("spoken_script"), duration
        )
        prompt = self._ugc_video_prompt(base_prompt, spoken_script, duration)

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
