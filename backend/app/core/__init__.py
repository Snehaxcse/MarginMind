"""Config, pipeline orchestrator, and shared constants.

The orchestrator is the only place that sequences:

message → intent → catalogue → recommend → signals → friction
→ proposed action → policy → approval → revalidate → pay → verify → audit
"""

from app.core.config import Settings, get_settings
from app.core.ref_ids import RefPrefix, basket_version_ref, format_ref_id, next_numeric_ref_id

__all__ = [
    "Settings",
    "get_settings",
    "RefPrefix",
    "basket_version_ref",
    "format_ref_id",
    "next_numeric_ref_id",
]
