"""Prefill a directory's form in the owner's Chrome, then stop before the submit button."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from db.social_engine import listings as listing_store

from ..activity import record
from ..context import StoreContext
from ..errors import ConfigurationError, ValidationError, describe
from .catalogue import Directory, get_directory
from .mapping import plan_by_rules, refine_with_model
from .profile import missing_fields, seeded_profile, values_for
from .relay import Relay

logger = logging.getLogger("ai_blog_server.social_engine.listings")


def relay_for(sctx: StoreContext) -> Relay:
    s = sctx.settings.listings
    return Relay(s.relay_dir, profile=s.relay_profile, timeout_seconds=s.relay_timeout_seconds)


def _scratch(sctx: StoreContext) -> Path:
    root = os.environ.get("SOCIAL_ENGINE_MEDIA_ROOT") or os.path.expanduser("~/.social-engine-media")
    return Path(root) / "listings" / sctx.store_id


async def fill_directory(sctx: StoreContext, directory_id: str, *, tab: str = "", use_model: bool = True) -> dict[str, Any]:
    """Fill the form for `directory_id` in Chrome. Returns what was filled and what is left for the owner."""
    directory = get_directory(directory_id)
    if not directory:
        raise ValidationError(f"Unknown directory '{directory_id}'.")
    if directory.fill not in ("relay",):
        raise ValidationError(f"{directory.name} is not filled through Chrome ({directory.fill}); follow its note instead.")
    profile = seeded_profile(sctx)
    gaps = missing_fields(profile)
    if gaps["required"]:
        raise ValidationError("Fill in the profile first: " + ", ".join(gaps["required"]) + ".")
    if directory.requires_address and not (profile.address_line1 and profile.postcode):
        raise ValidationError(f"{directory.name} needs a public address; add one to the profile or skip this directory.")

    subject = f"listing:{directory.id}"
    relay = relay_for(sctx)
    await listing_store.save_listing(sctx.store_id, directory.id, fill={"state": "running"})
    opened = False
    try:
        if not tab:
            found = await relay.find_tab(directory.hosts)
            if found:
                tab = str(found.get("tabId") or found.get("suggestedTargetId") or "")
        if not tab:
            tab = await relay.open(directory.submit_url)
            opened = True
        snapshot = await relay.snapshot(tab, interactive=True)
        values = values_for(profile, directory)
        plan = plan_by_rules(snapshot, values)
        if use_model and plan.unmatched:
            plan = await refine_with_model(sctx, plan, values, subject_id=subject)
        if not plan.fill:
            page = snapshot.url or directory.submit_url
            text_fields = [f for f in snapshot.fields() if f["role"] in {"textbox", "combobox"}]
            if not text_fields:
                message = (f"No form on this page ({page}). Sign in or click through to the add-business form in Chrome, "
                           "then press Fill again: the engine fills the tab that is already open.")
            else:
                message = "The form fields were found but none matched the profile; fill this one by hand."
            result = {"state": "no_form", "page": page, "message": message, **plan.summary()}
            await listing_store.save_listing(sctx.store_id, directory.id, fill=result)
            await record(sctx.store_id, "listing", directory.id, f"{directory.name}: {message}", level="warning",
                         stage="listings.fill", correlation_id=subject)
            return result
        await relay.fill(tab, [{"ref": p.ref, "value": p.value} for p in plan.fill], scratch_dir=_scratch(sctx))
        result = {"state": "filled", "page": snapshot.url or directory.submit_url, "tab": tab, **plan.summary()}
        result["message"] = (f"{len(plan.fill)} fields filled in Chrome. Check them, add anything in 'left for you', "
                             "then press the site's own submit button.")
        await listing_store.save_listing(sctx.store_id, directory.id, status="filled", fill=result)
        await record(sctx.store_id, "listing", directory.id,
                     f"{directory.name}: {len(plan.fill)} fields prefilled in Chrome; the submit is yours.",
                     stage="listings.fill", data=result, correlation_id=subject)
        return result
    except (ConfigurationError, ValidationError):
        await listing_store.save_listing(sctx.store_id, directory.id, fill={"state": "error", "message": "see activity"})
        raise
    except Exception as exc:
        kind, message = describe(exc)
        result = {"state": "error", "message": message, "page": directory.submit_url}
        await listing_store.save_listing(sctx.store_id, directory.id, fill=result)
        await record(sctx.store_id, "listing", directory.id, f"{directory.name}: fill failed ({message})", level="error",
                     stage="listings.fill", correlation_id=subject)
        raise
    finally:
        if opened and False:   # the tab stays open on purpose: the owner finishes there
            await relay.close(tab)


def directory_row(directory: Directory, listing: dict | None, profile_ready: bool, has_address: bool) -> dict:
    """What the Listings page shows for one directory."""
    listing = listing or {}
    fill = listing.get("fill") or {}
    status = listing.get("status") or ("live" if directory.fill == "done" else "todo")
    blocked = ""
    if directory.requires_address and not has_address:
        blocked = "needs a public address"
    elif directory.fill == "relay" and not profile_ready:
        blocked = "finish the profile first"
    return {
        "id": directory.id, "name": directory.name, "kind": directory.kind, "url": directory.url,
        "submit_url": directory.submit_url, "why": directory.why, "notes": directory.notes, "cost": directory.cost,
        "trust": directory.trust, "reach": directory.reach, "needs": list(directory.needs), "fill": directory.fill,
        "status": status, "listing_url": listing.get("listing_url") or (directory.submit_url if directory.fill == "done" else ""),
        "notes_owner": listing.get("notes") or "", "last_fill": fill, "last_check": listing.get("check") or {},
        "filled_at": listing.get("filled_at"), "submitted_at": listing.get("submitted_at"),
        "live_at": listing.get("live_at"), "checked_at": listing.get("checked_at"), "blocked": blocked,
    }
