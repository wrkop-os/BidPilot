"""Company Knowledge Base (PRD §6.5) — the only permissible source of
factual claims about the company."""

from .schema import CompanyProfile, KnowledgeBaseData
from .store import KnowledgeBase, load_kb

__all__ = ["CompanyProfile", "KnowledgeBaseData", "KnowledgeBase", "load_kb"]
