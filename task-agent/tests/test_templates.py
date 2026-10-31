from __future__ import annotations

import pytest

from task_agent.tasks import TASKS
from task_agent.templates import BUILTINS, TemplateError, TemplateStore, render, template_fields


def test_fields_from_placeholders():
    fields = template_fields("Find {how_many=5} in {location}. {details?} Again {location}.")
    assert fields == [
        {"name": "how_many", "label": "How many", "required": False, "default": "5"},
        {"name": "location", "label": "Location", "required": True, "default": ""},
        {"name": "details", "label": "Details", "required": False, "default": ""},
    ]


def test_render_fills_defaults_drops_empty_optionals_and_reports_missing():
    text, missing = render("Find {how_many=5} in {location}. {details?} Thanks.", {"location": "  Leeds\n"})
    assert text == "Find 5 in Leeds. Thanks."
    assert missing == []
    _, missing = render("Find {how_many=5} in {location}.", {})
    assert missing == ["location"]


@pytest.mark.parametrize("template", BUILTINS, ids=lambda t: t.key)
def test_every_builtin_builds_a_valid_job(store, template):
    values = {f["name"]: "Example" for f in template.fields}
    title, payload = TemplateStore(store).build_job(template, values)
    spec = TASKS[template.task_type]
    assert spec.payload_model.model_validate(payload)
    assert "{" not in payload[spec.prompt_field] and title


def test_missing_required_value_is_refused(store):
    templates = TemplateStore(store)
    with pytest.raises(TemplateError, match="Fill in: Event name"):
        templates.build_job(templates.get("find-event-listings"), {"event_type": "talk"})


def test_own_templates_save_update_delete(store):
    templates = TemplateStore(store)
    saved = templates.save(name="Venue search", description="", task_type="research", prompt="Venues in {town}")
    again = templates.save(name="Venue search", description="", task_type="research", prompt="Halls in {town}")
    assert saved.key == "venue-search" and again.key == "venue-search-2"
    updated = templates.save(key=saved.key, name="Venues", description="d", task_type="research", prompt="{town}?")
    assert updated.name == "Venues" and updated.fields[0]["name"] == "town"
    templates.delete(saved.key)
    with pytest.raises(LookupError):
        templates.get(saved.key)


def test_builtins_are_read_only_and_bad_defaults_are_refused(store):
    templates = TemplateStore(store)
    with pytest.raises(TemplateError, match="Built-in"):
        templates.save(key="research", name="x", description="", task_type="research", prompt="{q}")
    with pytest.raises(TemplateError, match="Built-in"):
        templates.delete("research")
    with pytest.raises(TemplateError, match="min_sources"):
        templates.save(name="x", description="", task_type="research", prompt="{q}", defaults={"min_sources": 50})
    with pytest.raises(TemplateError, match="Unknown task type"):
        templates.save(name="x", description="", task_type="nope", prompt="{q}")
