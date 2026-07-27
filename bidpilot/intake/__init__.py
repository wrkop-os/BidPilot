"""Intake (A1): SAM.gov retrieval with amendment-chain resolution."""

from .intake import (
    order_amendment_chain,
    register_manual_attachments,
    run_intake,
    unprocessed_manual_attachments,
)
from .samgov import SamGovClient, parse_notice_id

__all__ = [
    "run_intake",
    "order_amendment_chain",
    "register_manual_attachments",
    "unprocessed_manual_attachments",
    "SamGovClient",
    "parse_notice_id",
]
