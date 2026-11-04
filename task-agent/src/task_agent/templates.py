"""Task templates: a named prompt with blanks to fill in, for one task type.

Blanks in the prompt: `{name}` required, `{name?}` optional, `{name=default}` optional with a default.
Built-in templates live here; Joe's own templates are saved in the DB.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import ValidationError

from task_agent.db import JobStore, iso, now
from task_agent.tasks import TASKS, TEMPLATE_TYPES

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]{0,40})(\?|=([^{}]*))?\}")
_KEY = re.compile(r"[^a-z0-9]+")

BRAND = (
    "We are BioluxeLab (bioluxelab.com), a small UK wellness brand selling wellness products and food "
    "supplements online. Prefer UK options and official sources."
)


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    task_type: str
    prompt: str
    defaults: dict[str, Any] = field(default_factory=dict)
    builtin: bool = False
    enabled: bool = True
    updated_at: str | None = None

    @property
    def fields(self) -> list[dict[str, Any]]:
        return template_fields(self.prompt)

    def choices(self, options: dict[str, list[dict[str, str]]] | None) -> dict[str, list[dict[str, str]]]:
        """The list choices that apply to this template: only fields its task type takes as values (a research
        template's free-text "event_type", e.g. "talk", stays free text)."""
        spec = TASKS.get(self.task_type)
        takes = set(spec.value_fields) if spec else set()
        return {name: opts for name, opts in (options or {}).items() if name in takes}

    def to_dict(self, options: dict[str, list[dict[str, str]]] | None = None) -> dict[str, Any]:
        """As the admin page gets it. `options`: choices for fields like event_type (rendered as a list)."""
        mine = self.choices(options)
        fields = [{**f, "options": mine[f["name"]]} if f["name"] in mine else f for f in self.fields]
        return {**asdict(self), "fields": fields}


BUILTINS: tuple[Template, ...] = (
    Template(
        "research",
        "Research a question",
        "Ask anything. The agent searches the web, reads the pages and answers with sources.",
        "research",
        "{question}",
        {"min_sources": 2, "context": BRAND},
        builtin=True,
    ),
    Template(
        "find-event-listings",
        "Find places to list an event",
        "Free or cheap websites and local listings for an event, with what each needs before it goes live.",
        "research",
        "Find {how_many=5} free or low-cost websites, apps or local listings where we can list this event so "
        "local people find it: {event_name}, a {event_type} in {location} on {event_date}. {details?} "
        "For each one, give the link to start, what it costs, and what it needs before the listing goes live "
        "(account, approval, required details).",
        {"min_sources": 3, "context": BRAND},
        builtin=True,
    ),
    Template(
        "site-rules-and-prices",
        "Check a site's rules and prices",
        "What a website costs, its rules or limits, and what's needed to sign up.",
        "research",
        "What does it cost to use {website} to {purpose}? What are its rules or limits, and what do we need to "
        "sign up? Use {website}'s own help, pricing or terms pages where possible.",
        {"min_sources": 2, "context": BRAND},
        builtin=True,
    ),
    Template(
        "local-communities",
        "Find local groups to share with",
        "Active local groups, forums and newsletters where an event or offer could be shared.",
        "research",
        "Find {how_many=5} active local groups, forums, newsletters or community pages in {location} for people "
        "interested in {topic}, where a small business could share an event or offer. For each, give the link, "
        "roughly how active it is, and its rules about promotion.",
        {"min_sources": 3, "context": BRAND},
        builtin=True,
    ),
    Template(
        "competitor-check",
        "Look at a competitor",
        "What a competitor sells and promotes, at what prices, and on which channels.",
        "research",
        "Look at {competitor} {website?} using their public pages. What do they sell or promote, at what prices, "
        "which channels and events do they use, and what are they doing that we could learn from?",
        {"min_sources": 2, "context": BRAND},
        builtin=True,
    ),
    Template(
        "promote-event",
        "Promote an event",
        "Pick the event type and give it the event's links. It finds listing sites, Facebook groups and forums that "
        "suit the event, then posts it on each one by itself (groups at a careful pace) and on the type's own "
        "Facebook and LinkedIn Pages, using that type's email and logins.",
        "promote_event",
        "Promote this event everywhere suitable. Event type: {event_type}. Event links: {event_urls}. "
        "Our own event: {my_event=yes}. {notes?}",
        {"max_places": 15},
        builtin=True,
    ),
    Template(
        "health-claim-check",
        "Check a health claim (UK)",
        "Whether a supplement claim is allowed in the UK, its conditions of use, and safer wording.",
        "research",
        'Is this claim allowed for a food supplement sold in the UK: "{claim}" for {ingredient}? Check the GB '
        "Nutrition and Health Claims register and ASA/CAP guidance. Say whether it's authorised word for word, "
        "its conditions of use, and safer wording if it isn't allowed.",
        {"min_sources": 2, "context": BRAND},
        builtin=True,
    ),
)
BUILTIN_KEYS = {t.key for t in BUILTINS}


class TemplateError(ValueError):
    """A template or its values can't be used (message shown to the user as-is)."""


LABELS = {
    "event_type": "Event type",
    "event_urls": "Event links (one per line)",
    "post_from": "Accounts to post from (e.g. Facebook: joe.bains)",
    "tag": "Handles to tag",
    "signup_email": "Email for new sign-ups",
    "my_event": "Our own event?",
    "notes": "Notes",
}


def humanize(name: str) -> str:
    return LABELS.get(name) or name.replace("_", " ").strip().capitalize()


def is_multiline(name: str) -> bool:
    return name in {f for spec in TASKS.values() for f in spec.multiline_fields} or name.endswith(("_urls", "_notes"))


def template_fields(prompt: str) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for match in PLACEHOLDER.finditer(prompt):
        name, marker, default = match.group(1), match.group(2), match.group(3)
        if name in seen:
            continue
        seen[name] = {
            "name": name,
            "label": humanize(name),
            "required": marker is None,
            "default": (default or "").strip() if marker and marker.startswith("=") else "",
            "multiline": is_multiline(name),
        }
    return list(seen.values())


def render(prompt: str, values: dict[str, Any]) -> tuple[str, list[str]]:
    """Fill the blanks. Returns (text, names of required blanks left empty)."""
    clean = {k: " ".join(str(v).split()) for k, v in (values or {}).items() if v is not None}
    missing: list[str] = []

    def fill(match: re.Match[str]) -> str:
        name, marker, default = match.group(1), match.group(2), match.group(3)
        value = clean.get(name, "")
        if value:
            return value
        if marker is None:
            if name not in missing:
                missing.append(name)
            return "{" + name + "}"
        return (default or "").strip() if marker.startswith("=") else ""

    text = PLACEHOLDER.sub(fill, prompt)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return text.strip(), missing


class TemplateStore:
    def __init__(self, store: JobStore) -> None:
        self.store = store

    def field_options(self) -> dict[str, list[dict[str, str]]]:
        """Choices for list fields: the enabled event types."""
        from task_agent.event_types import EventTypeStore

        kinds = EventTypeStore(self.store).list(enabled_only=True)
        return {"event_type": [{"value": k["key"], "label": k["name"]} for k in kinds]}

    def list(self) -> list[Template]:
        with self.store._connect() as db:
            rows = db.execute("SELECT * FROM task_templates ORDER BY name COLLATE NOCASE").fetchall()
        return [*BUILTINS, *(self._from_row(r) for r in rows)]

    def get(self, key: str) -> Template:
        for template in self.list():
            if template.key == key:
                return template
        raise LookupError(f"No template '{key}'.")

    def save(
        self,
        *,
        name: str,
        description: str,
        task_type: str,
        prompt: str,
        defaults: dict[str, Any] | None = None,
        enabled: bool = True,
        key: str | None = None,
    ) -> Template:
        name, description, prompt = name.strip(), description.strip(), prompt.strip()
        defaults = dict(defaults or {})
        if not name:
            raise TemplateError("Give the template a name.")
        if key in BUILTIN_KEYS:
            raise TemplateError("Built-in templates can't be changed. Duplicate it and edit the copy.")
        spec = TASKS.get(task_type)
        if spec is None or task_type not in TEMPLATE_TYPES:
            raise TemplateError(f"Unknown task type '{task_type}'.")
        if not prompt:
            raise TemplateError("The prompt can't be empty.")
        if spec.prompt_field in defaults:
            raise TemplateError(f"'{spec.prompt_field}' comes from the prompt, not the defaults.")
        samples = {f["name"]: spec.sample_values.get(f["name"], "example value") for f in template_fields(prompt)}
        sample, _ = render(prompt, samples)
        raw = {k: v for k, v in {**spec.sample_values, **samples}.items() if k in spec.value_fields}
        try:
            spec.payload_model.model_validate({**defaults, **raw, spec.prompt_field: sample})
        except ValidationError as exc:
            raise TemplateError(f"These settings don't work for a {task_type} task: {_short(exc)}") from exc

        stamp = iso(now())
        with self.store._connect() as db:
            if key:
                updated = db.execute(
                    """UPDATE task_templates SET name = ?, description = ?, task_type = ?, prompt = ?, defaults = ?,
                           enabled = ?, updated_at = ? WHERE key = ?""",
                    (name, description, task_type, prompt, json.dumps(defaults), int(enabled), stamp, key),
                ).rowcount
                if not updated:
                    raise LookupError(f"No template '{key}'.")
            else:
                key = self._new_key(db, name)
                db.execute(
                    """INSERT INTO task_templates (key, name, description, task_type, prompt, defaults, enabled,
                           created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, name, description, task_type, prompt, json.dumps(defaults), int(enabled), stamp, stamp),
                )
        return self.get(key)

    def delete(self, key: str) -> None:
        if key in BUILTIN_KEYS:
            raise TemplateError("Built-in templates can't be deleted.")
        with self.store._connect() as db:
            if not db.execute("DELETE FROM task_templates WHERE key = ?", (key,)).rowcount:
                raise LookupError(f"No template '{key}'.")

    def build_job(self, template: Template, values: dict[str, Any], title: str = "") -> tuple[str, dict[str, Any]]:
        """Turn a template and filled-in values into (title, validated payload)."""
        spec = TASKS.get(template.task_type)
        if spec is None:
            raise TemplateError(f"Unknown task type '{template.task_type}'.")
        values = dict(values or {})
        shown = dict(values)
        if "event_type" in spec.value_fields and str(values.get("event_type") or "").strip():
            from task_agent.event_types import EventTypeStore

            kind = EventTypeStore(self.store).find(str(values["event_type"]))
            if kind is None:
                raise TemplateError(f"There's no event type '{values['event_type']}'. Add it in Settings first.")
            values["event_type"], shown["event_type"] = kind["key"], kind["name"]
        text, missing = render(template.prompt, shown)
        if missing:
            raise TemplateError("Fill in: " + ", ".join(humanize(m) for m in missing))
        # Some task types also take the values as they are (e.g. a list of event links), not only as prompt text.
        raw = {k: v for k, v in (values or {}).items() if k in spec.value_fields and str(v or "").strip()}
        try:
            payload = spec.payload_model.model_validate({**template.defaults, **raw, spec.prompt_field: text})
        except ValidationError as exc:
            raise TemplateError(f"This task can't be queued: {_short(exc)}") from exc
        return (title.strip() or default_title(template, shown, text))[:120], payload.model_dump(mode="json")

    @staticmethod
    def _new_key(db: Any, name: str) -> str:
        base = _KEY.sub("-", name.lower()).strip("-")[:40] or "template"
        key, n = base, 2
        while key in BUILTIN_KEYS or db.execute("SELECT 1 FROM task_templates WHERE key = ?", (key,)).fetchone():
            key, n = f"{base}-{n}", n + 1
        return key

    @staticmethod
    def _from_row(row: Any) -> Template:
        return Template(
            key=row["key"],
            name=row["name"],
            description=row["description"],
            task_type=row["task_type"],
            prompt=row["prompt"],
            defaults=json.loads(row["defaults"] or "{}"),
            builtin=False,
            enabled=bool(row["enabled"]),
            updated_at=row["updated_at"],
        )


def default_title(template: Template, values: dict[str, Any], text: str) -> str:
    if template.prompt.strip() in ("{question}", "{" + TASKS[template.task_type].prompt_field + "}"):
        return text
    first = next((str(values.get(f["name"])).strip() for f in template.fields if str(values.get(f["name"]) or "")), "")
    return f"{template.name}: {first}" if first else template.name


def _short(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())[:300]
