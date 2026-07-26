"""Specialized agents, one per pipeline stage.

Each agent is a function taking the LLM wrapper plus typed inputs and
returning a typed artifact (see ``bidpilot.models``). The pipeline in
``bidpilot.pipeline`` orchestrates them in order with human-in-the-loop
gates between stages.
"""

from .parser import analyze_solicitation
from .eligibility import check_eligibility
from .compliance import build_compliance_matrix
from .submission import extract_submission_instructions
from .technical import draft_volume
from .cost import estimate_cost
from .forms import prepare_forms

__all__ = [
    "analyze_solicitation",
    "check_eligibility",
    "build_compliance_matrix",
    "extract_submission_instructions",
    "draft_volume",
    "estimate_cost",
    "prepare_forms",
]
