"""Self-correction after a rejected review.

When the content guard or the AI reviewer rejects a draft, the engine fixes
the draft itself before anyone is asked: the captions are rewritten from the
reviewer's notes, and when the image was the problem the next product photo is
tried. Only a draft that still fails after `guards.max_review_rewrites` fixes
(or one the engine cannot fix, e.g. a claim printed on the only product photo)
waits for a person.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ai.base import GuardResult, ReviewResult
from ..ai.guard import CLAIM_CODES
from ..settings import EngineSettings

ATTEMPT_KEY = "review_attempt"      # post options: fixes already made for this draft
FEEDBACK_KEY = "review_feedback"    # post options: notes handed to the caption writer


@dataclass
class FixPlan:
    attempt: int                    # 1-based number of the fix about to be made
    feedback: str = ""              # notes for the caption writer ("" → captions are kept)
    next_image: bool = False        # render with the next product photo
    problems: list[str] = field(default_factory=list)

    def describe(self, max_attempts: int) -> str:
        what = []
        if self.feedback:
            what.append("rewriting the captions from its notes")
        if self.next_image:
            what.append("trying the next product photo")
        return f"Review found issues; {' and '.join(what)} (fix {self.attempt} of {max_attempts})"


def has_other_photo(products: list[dict], image_offset: int) -> bool:
    """True when rotating the image offset shows a photo not shown yet."""
    return any(image_offset + 1 < len([img for img in (p.get("images") or []) if img.get("url")]) for p in products)


def plan_fix(
    *,
    guard: GuardResult,
    review: ReviewResult | None,
    options: dict,
    settings: EngineSettings,
    products: list[dict],
    captions_source: str,
) -> FixPlan | None:
    """What the engine should change before generating again, or None to stop.

    None means either the draft is fine, the fixes are used up, or nothing
    the engine can change would help (then a person decides).
    """
    rejected = guard.hold or (review is not None and not review.approve)
    if not rejected:
        return None
    max_attempts = settings.guards.max_review_rewrites
    attempt = int(options.get(ATTEMPT_KEY) or 0)
    if attempt >= max_attempts:
        return None

    problems: list[str] = []
    for issue in guard.issues:
        if issue.severity == "block" or issue.code in CLAIM_CODES:
            problems.append(issue.message)
    if review is not None:
        problems.extend(i for i in review.issues if not i.startswith(("Vision", "Guard:")))
    problems = list(dict.fromkeys(p for p in problems if p))

    vision = bool(review and review.vision_critical)
    next_image = vision and has_other_photo(products, int(options.get("image_offset") or 0))
    if vision and not next_image:
        return None     # the only photo is the problem: the owner has to change the product's images
    text_problems = bool(problems) or (review is not None and not review.approve and not vision)
    rewrite = captions_source == "ai" and text_problems
    if not rewrite and not next_image:
        return None

    feedback = ""
    if rewrite:
        who = "brand and compliance reviewer" if review is not None else "content guard"
        score = f" (score {review.score}/10)" if review is not None else ""
        lines = [f"The {who} rejected the previous draft{score}. Fix every point below; keep what was not criticised."]
        lines.extend(f"- {problem}" for problem in problems[:10])
        if review is not None and review.suggestions:
            lines.append("Suggested edits:")
            lines.extend(f"- {suggestion}" for suggestion in review.suggestions[:5])
        lines.append("Only state facts that appear in the product data. If a detail is not there, leave it out.")
        feedback = "\n".join(lines)
    return FixPlan(attempt=attempt + 1, feedback=feedback, next_image=next_image, problems=problems)
