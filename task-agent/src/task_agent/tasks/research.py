"""`research`: answer a question from the web, read-only, citing only pages it actually opened."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, Tool

from task_agent.tasks.base import JobDeps, TaskSpec
from task_agent.tools import web


class ResearchPayload(BaseModel):
    question: str = Field(min_length=5, max_length=2_000)
    context: str = Field("", max_length=4_000, description="Background Joe wants the agent to know")
    min_sources: int = Field(2, ge=1, le=8)


class Source(BaseModel):
    url: str
    title: str = ""
    used_for: str = Field("", description="Which part of the answer this source supports")


class ResearchResult(BaseModel):
    answer: str = Field(description="Direct answer to the question in plain English")
    key_points: list[str] = Field(description="The important facts, each traceable to a source")
    sources: list[Source] = Field(description="Only pages you opened with fetch_page during this task")
    confidence: Literal["high", "medium", "low"]
    gaps: list[str] = Field(default_factory=list, description="Anything you could not confirm")


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."), path, parts.query, ""))


async def web_search(ctx: RunContext[JobDeps], query: str) -> str:
    """Search the web. Returns titles, links and snippets. Open promising links with fetch_page before
    relying on them — snippets alone are not sources."""
    return await web.search(query)


async def fetch_page(ctx: RunContext[JobDeps], url: str) -> str:
    """Open a public web page and return its readable text."""
    try:
        final_url, text = await web.fetch_text(url, ctx.deps.settings.fetch_max_chars, client=ctx.deps.http)
    except web.BlockedURL as exc:
        return f"[Blocked: {exc}]"
    except httpx.HTTPError as exc:
        return f"[Could not load {url}: {type(exc).__name__}]"
    ctx.deps.opened_urls.update({normalize_url(url), normalize_url(final_url)})
    return text


def check_sources(ctx: RunContext[JobDeps], result: ResearchResult) -> ResearchResult:
    """Verify-after-act for research: every cited source must be a page the agent opened."""
    unopened = [s.url for s in result.sources if normalize_url(s.url) not in ctx.deps.opened_urls]
    if unopened:
        raise ModelRetry(
            "These sources were never opened with fetch_page during this task: "
            + ", ".join(unopened)
            + ". Open them and check they support your answer, or remove them."
        )
    if not result.sources and result.confidence != "low":
        raise ModelRetry("A result with no opened sources must have confidence 'low'.")
    return result


def render_prompt(payload: ResearchPayload) -> str:
    context = f"\nBackground from Joe:\n{payload.context}\n" if payload.context else ""
    return (
        f"Research question:\n{payload.question}\n{context}\n"
        "How to work:\n"
        "- Use web_search to find candidate sources, then open the most relevant ones with fetch_page and read them.\n"
        f"- Open at least {payload.min_sources} different sources if they exist. Prefer official and primary sources.\n"
        "- If sources disagree, say so and explain which you trust more and why.\n"
        "- When you have enough evidence, return the final result."
    )


def _configure(agent: Agent[JobDeps, ResearchResult]) -> None:
    agent.output_validator(check_sources)


RESEARCH = TaskSpec(
    type="research",
    summary="Answer a question from the web (read-only), with checked sources",
    instructions="Your job: research a question on the public web and report what you found, with sources.",
    payload_model=ResearchPayload,
    output_type=ResearchResult,
    tools=(Tool(web_search, takes_ctx=True), Tool(fetch_page, takes_ctx=True)),
    render_prompt=render_prompt,
    configure=_configure,
)
