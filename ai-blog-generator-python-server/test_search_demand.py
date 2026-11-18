"""Search demand: real phrases from Search Console → refreshed posts and new-post topics."""
from __future__ import annotations

import json

import pytest

import db
from services.seo_growth import search_demand as sd

BASE = "https://bioluxelab.com"
MATS = "/blogs/home-fitness-mobility-hub/best-yoga-mats-for-beginners"
SLEEP = "/blogs/sleep-recovery-hub/best-at-home-tools-for-sleep-improvement"
ANSWER = ("A beginner does best with a mat about four to six millimetres thick, with a surface that grips when "
          "hands are damp and enough length to lie flat. Choose cushioning for knees over a thin travel mat, check "
          "that it lies flat on your floor, and pick a material that wipes clean after each session at home.")


def _row(query, page, impressions, position, clicks=0):
    return {"query": query, "page": BASE + page, "impressions": impressions, "position": position, "clicks": clicks}


ROWS = [
    _row("best yoga mat for beginners", MATS, 627, 8.5),
    _row("yoga mat for beginners", MATS, 222, 14.0, 1),
    _row("best yoga mat for new dads", MATS, 87, 3.4),
    _row("genealogy equipment for light sleepers", SLEEP, 164, 5.0),
    _row("equipment for light sleepers", SLEEP, 60, 9.0),
    _row("red light therapy at home full body", "/blogs/pain-relief-posture-fix-hub/red-light", 113, 44.0),
    _row("bioluxe tart cherries", "/products/tart-cherries", 90, 4.0),
    _row("mats", MATS, 500, 6.0),
    _row("full body red light therapy at home", "/blogs/pain-relief-posture-fix-hub/red-light", 30, 41.0),
    _row("yoga mats for beginners", "/collections/yoga-practice", 45, 33.0),
    _row("best yoga mat for beginners", "/fr" + MATS, 40, 30.0),
]


class FakeModel:
    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    async def generate_raw(self, prompt, system_prompt=""):
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return answer if isinstance(answer, str) else json.dumps(answer)


class FakeShop:
    """Stands in for Shopify's GraphQL: one article per path, updates applied to it."""
    def __init__(self, paths):
        self.articles = {
            p: {"id": f"gid://shopify/Article/{i}", "handle": p.split("/")[-1], "title": f"Post {i}",
                "body": "<p>Intro paragraph.</p><h2>Details</h2><p>More.</p>", "isPublished": True,
                "blog": {"handle": p.split("/")[2]}, "titleTag": None, "descriptionTag": None}
            for i, p in enumerate(paths, start=1)
        }
        self.deleted = []

    async def __call__(self, store, query, variables=None, timeout=45):
        if "articles(" in query:
            handle = variables["q"].split(":", 1)[1]
            return {"articles": {"nodes": [a for a in self.articles.values() if a["handle"] == handle]}}
        if "articleUpdate" in query:
            article = next(a for a in self.articles.values() if a["id"] == variables["id"])
            article["body"] = variables["article"]["body"]
            for field in variables["article"].get("metafields", []):
                article["titleTag" if field["key"] == "title_tag" else "descriptionTag"] = {"value": field["value"]}
            return {"articleUpdate": {"article": {"id": article["id"]}, "userErrors": []}}
        if "metafieldsDelete" in query:
            self.deleted += [m["key"] for m in variables["m"]]
            return {"metafieldsDelete": {"userErrors": []}}
        raise AssertionError(query)


@pytest.fixture
def world(se_db, monkeypatch):
    shop = FakeShop([MATS, SLEEP])
    model = FakeModel([{"real": [1, 2, 4, 5]}])     # set per test where it matters
    rows = {"value": ROWS}

    async def search_rows(store_id, rules):
        return rows["value"]

    async def the_model(store_id, rules):
        return model

    monkeypatch.setattr(sd, "_search_rows", search_rows)
    monkeypatch.setattr(sd, "_model", the_model)
    monkeypatch.setattr(sd.shopify_client, "graphql_request", shop)
    return {"shop": shop, "model": model, "rows": rows, "store": se_db}


GOOD = {"seo_title": "Best Yoga Mat for Beginners: How to Choose", "question": "What is the best yoga mat for beginners?",
        "meta_description": "Thickness, grip and size explained simply, so you can pick a first yoga mat that suits home practice.",
        "answer": ANSWER}


def test_shipped_rules_file_is_valid():
    rules = sd.load_rules()
    assert rules.refresh.position_from <= rules.refresh.position_to and rules.refresh.posts_per_run >= 1


def test_phrases_are_summarised_per_phrase_with_the_page_shown_most():
    phrases = {p.text: p for p in sd.summarise(ROWS)}
    best = phrases["best yoga mat for beginners"]
    assert (best.impressions, best.page, best.position, best.best_position) == (667, MATS, 8.5, 8.5)
    rules = sd.load_rules().phrases
    assert not sd.passes_word_rules("mats", rules)                       # one word
    assert not sd.passes_word_rules("bioluxe tart cherries", rules)      # the shop's own name
    assert sd.passes_word_rules("best yoga mat for beginners", rules)


def test_refresh_plan_takes_the_most_searched_phrase_per_post_and_skips_recent_ones():
    rules = sd.load_rules()
    phrases = [p for p in sd.summarise(ROWS) if sd.passes_word_rules(p.text, rules.phrases)]
    plans = sd.plan_refreshes(phrases, rules.refresh, recently_touched=set())
    assert [(p.path, p.phrase.text) for p in plans][0] == (MATS, "best yoga mat for beginners")
    assert "yoga mat for beginners" in [p.text for p in plans[0].also]
    assert all(not p.path.startswith("/fr/") and p.path.startswith("/blogs/") for p in plans)
    assert MATS not in [p.path for p in sd.plan_refreshes(phrases, rules.refresh, recently_touched={MATS})]
    # One phrase per topic, and none for a topic that already has a page near page one under other wording.
    assert [p.text for p in sd.uncovered(phrases, rules.new_posts, known=phrases)] == ["red light therapy at home full body"]


@pytest.mark.asyncio
async def test_model_filters_machine_made_phrases_once_and_remembers(world):
    rules = sd.load_rules()
    phrases = [p for p in sd.summarise(ROWS) if p.page in (MATS, SLEEP)]
    judged = [p for p in phrases if sd.passes_word_rules(p.text, rules.phrases)]
    order = [p.text for p in judged]
    world["model"].answers = [{"real": [i + 1 for i, t in enumerate(order) if "genealogy" not in t and "new dads" not in t]}]
    real = await sd.real_phrases(world["store"], phrases, {MATS: "Best Yoga Mats for Beginners"}, rules)
    assert {"genealogy equipment for light sleepers", "best yoga mat for new dads", "mats"}.isdisjoint(p.text for p in real)
    assert "best yoga mat for beginners" in [p.text for p in real]
    assert "shown with: Best Yoga Mats for Beginners" in world["model"].prompts[0]
    await sd.real_phrases(world["store"], phrases, {}, rules)
    assert len(world["model"].prompts) == 1                              # remembered


def test_model_text_is_checked_before_it_is_used():
    rules = sd.load_rules().refresh
    assert sd.checked_refresh(GOOD, "best yoga mat for beginners", rules)["seo_title"] == GOOD["seo_title"]
    for change, reason in (
        ({"seo_title": "x" * 80}, "search title"), ({"meta_description": "y" * 200}, "description"),
        ({"answer": "Too short."}, "words"), ({"question": ""}, "missing"),
        ({"answer": ANSWER + " <b>Buy</b>"}, "HTML"), ({"answer": ANSWER.replace("grips", "cures arthritis and grips")}, "laim"),
    ):
        with pytest.raises(ValueError, match=reason):
            sd.checked_refresh({**GOOD, **change}, "best yoga mat for beginners", rules)


def test_answer_goes_after_the_first_paragraph_and_replaces_an_older_one():
    body = sd.with_answer("<p>Intro.</p><h2>Next</h2>", "Which mat?", "A & B", "yoga mat")
    assert body.startswith(f'<p>Intro.</p><div class="{sd.ANSWER_CLASS}"') and "A &amp; B" in body and body.endswith("<h2>Next</h2>")
    again = sd.with_answer(body, "Which mat now?", "C", "yoga mat")
    assert again.count(sd.ANSWER_CLASS) == 1 and "Which mat now?" in again and "A &amp; B" not in again


@pytest.mark.asyncio
async def test_cycle_refreshes_a_post_keeps_the_old_version_and_can_put_it_back(world):
    world["model"].answers = [{"real": list(range(1, 9))}, {**GOOD, "seo_title": "x" * 90}, GOOD]
    plan = await sd.run_cycle(world["store"], apply=False)
    assert plan["status"] == "planned" and plan["plan"][0]["path"] == MATS
    assert not await db.list_seo_refreshes(world["store"])
    world["model"].answers = [GOOD]

    done = await sd.run_cycle(world["store"])
    applied = [r for r in done["results"] if r["status"] == "applied"]
    assert applied and applied[0]["path"] == MATS
    article = world["shop"].articles[MATS]
    assert article["titleTag"]["value"] == GOOD["seo_title"] and sd.ANSWER_CLASS in article["body"]
    assert article["body"].startswith("<p>Intro paragraph.</p><div")

    again = await sd.run_cycle(world["store"], apply=False)               # not touched again inside the wait
    assert MATS not in [p["path"] for p in again["plan"]]

    entry = next(e for e in await db.list_seo_refreshes(world["store"]) if e["path"] == MATS)
    assert entry["before"]["body"] == "<p>Intro paragraph.</p><h2>Details</h2><p>More.</p>"
    assert (await sd.restore_refresh(world["store"], entry["id"]))["restored"]
    assert article["body"] == entry["before"]["body"] and sorted(world["shop"].deleted) == ["description_tag", "title_tag"]
    assert not (await sd.restore_refresh(world["store"], entry["id"]))["restored"]


@pytest.mark.asyncio
async def test_a_refused_model_answer_changes_nothing(world):
    world["model"].answers = [{"real": list(range(1, 9))}, {**GOOD, "answer": "Too short."}]
    done = await sd.run_cycle(world["store"])
    assert all(r["status"] == "skipped" for r in done["results"])
    assert world["shop"].articles[MATS]["titleTag"] is None and not await db.list_seo_refreshes(world["store"])


@pytest.mark.asyncio
async def test_title_generator_is_told_the_real_searches_without_a_page(world):
    world["model"].answers = [{"real": list(range(1, 9))}]
    text = await sd.title_prompt_addition(world["store"])
    assert "red light therapy at home full body (113 searches shown, best position 44.0)" in text
    assert "best yoga mat for beginners" not in text                       # it already has a page near page one

    async def not_connected(store_id, rules):
        return None

    import services.seo_growth.search_demand as module
    module._search_rows, keep = not_connected, module._search_rows
    try:
        assert await sd.title_prompt_addition(world["store"]) == ""
        assert (await sd.run_cycle(world["store"]))["status"] == "not_connected"
    finally:
        module._search_rows = keep


@pytest.mark.asyncio
async def test_a_model_that_does_not_answer_is_asked_again(world):
    import httpx

    calls = {"n": 0}
    answers = [{"real": list(range(1, 11))}, httpx.ReadTimeout(""), GOOD]

    async def flaky(prompt, system_prompt=""):
        calls["n"] += 1
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer)

    world["model"].generate_raw = flaky
    done = await sd.run_cycle(world["store"])
    assert done["results"][0]["status"] == "applied" and calls["n"] >= 3
