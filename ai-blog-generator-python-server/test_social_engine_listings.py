"""Directory listings: profile, catalogue, form mapping, Chrome prefill, upkeep and the presence check."""
from __future__ import annotations

import json
import time

import httpx
import pytest

from db.social_engine import listings as listing_store
from db.social_engine import observability
from services.social_engine import http as se_http
from services.social_engine.context import load_store_context
from services.social_engine.errors import ValidationError
from services.social_engine.listings import checks, fill, mapping, profile as profile_mod
from services.social_engine.listings.catalogue import DIRECTORIES, KINDS, get_directory, ranked
from services.social_engine.listings.relay import Snapshot, parse_links, parse_refs
from services.social_engine.settings import ListingProfile, load_settings, save_settings
from test_social_engine_ai import FakeLLM

PROFILE = {
    "business_name": "Bio Luxe Lab", "tagline": "Wellness, yoga and recovery essentials",
    "short_description": "A London wellness shop for yoga, recovery and calm evenings.",
    "description": "Bio Luxe Lab sells yoga mats, foam rollers, massage tools and meditation cards. Founded in London. "
                   "Coaching and workshops with Joe Bains.",
    "long_description": "Paragraph one. Paragraph two. Paragraph three.",
    "email": "hello@bioluxelab.com", "phone": "+44 20 7946 0000", "website": "https://bioluxelab.com",
    "founder_name": "Joe Bains", "postcode": "SW1A 1AA", "address_line1": "1 Example Street",
    "social_links": {"instagram": "https://instagram.com/bioluxelab"},
    "keywords": ["yoga mat london", "foam roller"], "services": ["Biohacking coaching"],
}

FORM = Snapshot(url="https://www.freeindex.co.uk/add-business.htm", refs={
    "e1": {"role": "textbox", "name": "Business name *"},
    "e2": {"role": "textbox", "name": "Email address"},
    "e3": {"role": "textbox", "name": "Telephone"},
    "e4": {"role": "textbox", "name": "Website"},
    "e5": {"role": "textbox", "name": "Describe your business"},
    "e6": {"role": "textbox", "name": "Postcode"},
    "e7": {"role": "textbox", "name": "Password"},
    "e8": {"role": "textbox", "name": "How did you hear about us?"},
    "e9": {"role": "checkbox", "name": "I agree to the terms"},
    "e10": {"role": "textbox", "name": "First name"},
    "e11": {"role": "textbox", "name": "Instagram"},
    "e12": {"role": "button", "name": "Submit"},
})


class FakeRelay:
    def __init__(self, snapshot: Snapshot, tabs: list[dict] | None = None) -> None:
        self.snapshot_value = snapshot
        self.tabs_value = tabs or []
        self.opened: list[str] = []
        self.filled: list[dict] = []
        self.closed: list[str] = []

    async def tabs(self):
        return self.tabs_value

    async def find_tab(self, hosts):
        for tab in self.tabs_value:
            if any(h in tab["url"] for h in hosts):
                return tab
        return None

    async def open(self, url):
        self.opened.append(url)
        return "t1"

    async def close(self, tab):
        self.closed.append(tab)

    async def snapshot(self, tab, *, interactive=True, urls=False, limit=600):
        return self.snapshot_value

    async def fill(self, tab, fields, *, scratch_dir):
        self.filled = fields
        return {"ok": True}


async def _ready(store_id: str) -> None:
    await save_settings(store_id, {"listings": {"profile": PROFILE}, "ai": {"ai_decisions": False}})


# ---------------------------------------------------------------- catalogue and profile

def test_catalogue_is_consistent():
    ids = [d.id for d in DIRECTORIES]
    assert len(ids) == len(set(ids))
    for d in DIRECTORIES:
        assert d.kind in KINDS and d.url.startswith("https://") and d.submit_url.startswith("https://")
        assert 1 <= d.trust <= 10 and 1 <= d.reach <= 10 and d.fill in ("relay", "manual", "feed", "done")
        assert d.hosts and d.why
    assert ranked()[0].id in ("google_business", "trustpilot")
    assert get_directory("trustpilot").limits["description"] == 1000
    assert get_directory("nope") is None


@pytest.mark.asyncio
async def test_profile_is_seeded_from_the_brand_and_reports_gaps(se_db):
    await save_settings(se_db, {"brand": {"business_name": "Bio Luxe Lab", "slogan": "Calm evenings", "description": "We sell mats."}})
    sctx = await load_store_context(se_db)
    seeded = profile_mod.seeded_profile(sctx)
    assert seeded.business_name == "Bio Luxe Lab" and seeded.tagline == "Calm evenings"
    assert seeded.website == "https://bioluxelab.com" and seeded.description == "We sell mats."
    gaps = profile_mod.missing_fields(seeded)
    assert "email" in gaps["required"] and "phone" in gaps["required"] and "short description" in gaps["required"]
    assert "founder bio" in gaps["recommended"]


def test_descriptions_are_cut_to_each_directory_limit_at_a_sentence():
    prof = ListingProfile.model_validate(PROFILE)
    short = get_directory("scoot")                    # description limit 500
    assert profile_mod.description_for(prof, short) == PROFILE["description"]
    tight = get_directory("scoot").__class__(**{**get_directory("scoot").__dict__, "limits": {"description": 60}})
    cut = profile_mod.description_for(prof, tight)
    assert len(cut) <= 60 and cut.endswith(".")
    values = profile_mod.values_for(prof, short)
    assert values["founder_first_name"] == "Joe" and values["founder_last_name"] == "Bains"
    assert values["social_instagram"] == "https://instagram.com/bioluxelab" and values["category"] == "Health and wellness shop"
    assert values["keywords"] == "yoga mat london, foam roller" and "full_address" in values


# ---------------------------------------------------------------- mapping

def test_form_labels_map_to_profile_fields_and_sensitive_fields_are_left_alone():
    values = profile_mod.values_for(ListingProfile.model_validate(PROFILE), get_directory("freeindex"))
    plan = mapping.plan_by_rules(FORM, values)
    by_label = {p.label: p.key for p in plan.fill}
    assert by_label == {
        "Business name *": "business_name", "Email address": "email", "Telephone": "phone", "Website": "website",
        "Describe your business": "description", "Postcode": "postcode", "First name": "founder_first_name",
        "Instagram": "social_instagram",
    }
    assert [u["label"] for u in plan.unmatched] == ["How did you hear about us?"]
    assert plan.left_for_you[0]["label"] == "Password" and "yours to type" in plan.left_for_you[0]["reason"]
    assert all(p.label != "I agree to the terms" for p in plan.fill)      # checkboxes are never ticked
    assert mapping.match_label("Company Name:") == "business_name" and mapping.match_label("Last name") == "founder_last_name"
    assert mapping.match_label("Year established") == "founded_year" and mapping.match_label("Zip") == "postcode"


@pytest.mark.asyncio
async def test_model_refines_only_unmatched_fields_and_cannot_invent_refs(se_db):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    values = profile_mod.values_for(ListingProfile.model_validate(PROFILE), get_directory("freeindex"))
    plan = mapping.plan_by_rules(FORM, values)
    FakeLLM({"matches": [{"ref": "e8", "field": "website"}, {"ref": "e99", "field": "email"}, {"ref": "e7", "field": "phone"}]}).install()
    refined = await mapping.refine_with_model(sctx, plan, values, subject_id="t")
    model_fills = [p for p in refined.fill if p.source == "model"]
    assert [(p.ref, p.key) for p in model_fills] == [("e8", "website")]
    assert refined.unmatched == []


# ---------------------------------------------------------------- filling through Chrome

@pytest.mark.asyncio
async def test_fill_prefills_the_form_in_an_open_tab_and_leaves_the_submit(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    relay = FakeRelay(FORM, tabs=[{"tabId": "t7", "url": "https://www.freeindex.co.uk/add-business.htm"}])
    monkeypatch.setattr(fill, "relay_for", lambda sctx: relay)
    result = await fill.fill_directory(sctx, "freeindex", use_model=False)
    assert result["state"] == "filled" and result["tab"] == "t7" and relay.opened == []   # reused the open tab
    assert {f["ref"] for f in relay.filled} == {"e1", "e2", "e3", "e4", "e5", "e6", "e10", "e11"}
    assert relay.closed == []                                                            # the owner finishes there
    row = await listing_store.get_listing(se_db, "freeindex")
    assert row["status"] == "filled" and row["filled_at"] and row["fill"]["filled"][0]["field"] == "business_name"
    activity = await observability.list_activity(store_id=se_db, limit=5)
    assert any("prefilled in Chrome" in a["message"] for a in activity)


@pytest.mark.asyncio
async def test_fill_opens_the_site_and_explains_when_there_is_no_form(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    relay = FakeRelay(Snapshot(url="https://www.yell.com/", refs={"e1": {"role": "link", "name": "Add your business"}}))
    monkeypatch.setattr(fill, "relay_for", lambda sctx: relay)
    result = await fill.fill_directory(sctx, "yell", use_model=False)
    assert result["state"] == "no_form" and relay.opened == ["https://business.yell.com/free-listing/"]
    assert "press Fill again" in result["message"]
    assert (await listing_store.get_listing(se_db, "yell"))["status"] == "todo"


@pytest.mark.asyncio
async def test_fill_refuses_without_the_required_profile_or_a_needed_address(se_db, monkeypatch):
    sctx = await load_store_context(se_db)
    with pytest.raises(ValidationError, match="Fill in the profile first"):
        await fill.fill_directory(sctx, "freeindex")
    await save_settings(se_db, {"listings": {"profile": {**PROFILE, "address_line1": "", "postcode": ""}}})
    sctx = await load_store_context(se_db)
    with pytest.raises(ValidationError, match="public address"):
        await fill.fill_directory(sctx, "yelp")
    with pytest.raises(ValidationError, match="not filled through Chrome"):
        await fill.fill_directory(sctx, "google_business")


# ---------------------------------------------------------------- the table and upkeep

@pytest.mark.asyncio
async def test_listing_rows_stamp_status_times_and_select_due_checks(se_db):
    row = await listing_store.save_listing(se_db, "yell", status="submitted", notes="done 2 Nov")
    assert row["submitted_at"] and row["status"] == "submitted" and row["fill"] == {}
    row = await listing_store.save_listing(se_db, "yell", status="live", listing_url="https://www.yell.com/biz/x")
    assert row["live_at"] and row["submitted_at"]
    with pytest.raises(ValueError):
        await listing_store.save_listing(se_db, "yell", status="nonsense")
    assert [r["directory_id"] for r in await listing_store.due_for_check(se_db, older_than=int(time.time()) - 86400)] == ["yell"]
    await listing_store.save_listing(se_db, "yell", checked_at=int(time.time()))
    assert await listing_store.due_for_check(se_db, older_than=int(time.time()) - 86400) == []
    rows = fill.directory_row(get_directory("apprentus"), None, True, False)
    assert rows["status"] == "live" and rows["listing_url"].startswith("https://www.apprentus.com")
    assert fill.directory_row(get_directory("nextdoor"), None, True, False)["blocked"] == "needs a public address"
    assert fill.directory_row(get_directory("yell"), None, False, False)["blocked"] == "finish the profile first"


@pytest.mark.asyncio
async def test_live_checks_notice_a_removed_listing(se_db):
    await _ready(se_db)
    pages = {
        "/biz/ok": httpx.Response(200, text="<h1>Bio Luxe Lab</h1> call 020 7946 0000"),
        "/biz/gone": httpx.Response(404, text="not found"),
        "/biz/wall": httpx.Response(200, text="<h1>Sign in to continue</h1>"),
    }
    se_http.TRANSPORT_OVERRIDES["listing_check"] = httpx.MockTransport(lambda r: pages[r.url.path])
    for did, path in (("yell", "/biz/ok"), ("cylex", "/biz/gone"), ("hotfrog", "/biz/wall")):
        await listing_store.save_listing(se_db, did, status="live", listing_url=f"https://example.test{path}")
    result = await checks.check_due(se_db)
    assert result == {"checked": 3, "broken": 2, "due": 3}
    rows = await listing_store.list_listings(se_db)
    assert rows["yell"]["status"] == "live" and rows["yell"]["check"]["phone_found"] is True
    assert rows["cylex"]["status"] == "broken" and "gone" in rows["cylex"]["check"]["message"]
    assert rows["hotfrog"]["status"] == "broken" and "business name is not on it" in rows["hotfrog"]["check"]["message"]
    assert await checks.check_due(se_db) == {"checked": 0, "broken": 0, "due": 0}      # not due again today


# ---------------------------------------------------------------- what a client finds

SEARCH_TEXT = """- link "Bio Luxe Lab: Wellness bioluxelab.com https://bioluxelab.com" [ref=e31]
1. Accessibility help -> https://support.google.com/websearch/answer/181196?hl=en-GB
2. Bio Luxe Lab: Wellness, Yoga & Recovery Essentials -> https://bioluxelab.com/
3. How to Create a Daily Wellbeing Routine -> https://www.instagram.com/p/DbNGNtkjcxf/
4. Sleep Optimization Habits | Bio Luxe Lab -> https://www.linkedin.com/posts/bioluxelab_how-to
5. Beginner mobility -> https://bioluxelab.substack.com/p/beginner-at-home-mob
6. Bio Luxe Lab Reviews -> https://uk.trustpilot.com/review/bioluxelab.com
7. Shopping -> https://www.google.com/search?q=x&udm=28
"""


def test_snapshot_parsing_reads_refs_and_result_links():
    refs = parse_refs('- textbox "Business name" [ref=e3]\n- link "Learn more" [ref=e1]\n- textbox "Say \\"hi\\"" [ref=e4]')
    assert refs["e3"] == {"role": "textbox", "name": "Business name"} and refs["e4"]["name"] == 'Say "hi"'
    links = parse_links(SEARCH_TEXT)
    assert [l["url"] for l in links][:2] == ["https://support.google.com/websearch/answer/181196?hl=en-GB", "https://bioluxelab.com/"]
    assert links[1]["title"].startswith("Bio Luxe Lab: Wellness")


@pytest.mark.asyncio
async def test_presence_classifies_results_and_scores_how_established_the_brand_looks(se_db, monkeypatch):
    await _ready(se_db)
    sctx = await load_store_context(se_db)
    classified = checks.classify_links(parse_links(SEARCH_TEXT), sctx)
    assert classified["store_found"] and classified["directories_found"] == ["LinkedIn company page", "Trustpilot"]
    assert classified["social"] == ["bioluxelab.substack.com", "instagram.com"]
    assert "Google Business Profile" in classified["missing"] and all("google" not in r["host"] for r in classified["results"])
    score = classified["established"]
    assert score["score"] == 25 + 20 + 10 + 10 and score["next"][0].startswith("Keep the Google Business Profile")

    relay = FakeRelay(Snapshot(text=SEARCH_TEXT, links=parse_links(SEARCH_TEXT)))
    monkeypatch.setattr(fill, "relay_for", lambda sctx: relay)
    await listing_store.save_listing(se_db, "google_business", status="live", listing_url="https://g.page/bioluxelab")
    report = await checks.search_presence(sctx)
    assert len(report["queries"]) == 2 and report["queries"][1]["query"] == '"Joe Bains" coach'
    # Google's own panel is a card, not a link: the listing marked live counts (+20), as does the Apprentus profile.
    assert relay.closed == ["t1", "t1"] and report["summary"]["established"]["score"] == 85
    assert "Google Business Profile" in report["summary"]["directories_found"] and "Apprentus" in report["summary"]["directories_found"]
    assert (await checks.last_presence(se_db))["summary"]["store_found"] is True


# ---------------------------------------------------------------- Gemma writes the copy

@pytest.mark.asyncio
async def test_suggested_copy_is_checked_for_claims(se_db):
    await save_settings(se_db, {"brand": {"business_name": "Bio Luxe Lab", "what_you_sell": "yoga and recovery kit"}})
    sctx = await load_store_context(se_db)
    llm = FakeLLM(json.dumps({
        "tagline": "Wellness, yoga and recovery essentials", "short_description": "A London wellness shop.",
        "description": "Mats, rollers and meditation cards that cure insomnia.", "long_description": "One. Two. Three.",
        "founder_bio": "Joe Bains runs Bio Luxe Lab.", "keywords": ["yoga mat london"], "categories": ["Wellness shop"],
    })).install()
    result = await profile_mod.suggest_copy(sctx, products=[{"title": "Yoga mat", "product_type": "Yoga"}])
    assert result["copy"]["tagline"] == "Wellness, yoga and recovery essentials"
    assert result["warnings"] and result["warnings"][0].startswith("description:")
    assert "never promise a health result" in llm.prompt() and "Yoga mat" in llm.prompt()
