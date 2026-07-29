"""Onboarding interview agent (PRD §6.5): asks the questions a proposal
consultant would ask a new client, targeted at whatever the KB is missing."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..routing import ModelRouter, Tier
from .store import KnowledgeBase

SYSTEM = """You are a proposal consultant onboarding a new government-contractor
client. Given their current knowledge base, produce the interview questions needed
to fill the gaps — the kind of questions that unblock proposal writing and pricing:
identity/registration, certifications and expiry dates, size data per NAICS,
indirect rate structure, labor categories and rates, past performance details,
key personnel, reusable content, bonding/insurance/clearances. Ask only about
what's actually missing or stale; group related questions; order by importance."""


class InterviewQuestion(BaseModel):
    topic: str
    question: str
    why_it_matters: str
    fills_kb_field: str = Field(description="Which KB field/record this answer populates")


class InterviewPlan(BaseModel):
    questions: list[InterviewQuestion] = Field(default_factory=list)


def build_interview(router: ModelRouter, kb: KnowledgeBase) -> InterviewPlan:
    prompt = f"""Current knowledge base contents:

{kb.citable_corpus()[:100000]}

Stale/unverified entries:
{chr(10).join(kb.stale_entries()) or "(none)"}

Produce the gap-filling interview."""
    return router.structured(
        Tier.FRONTIER,
        system=SYSTEM,
        prompt=prompt,
        output_type=InterviewPlan,
        stage="kb.interview",
    )
