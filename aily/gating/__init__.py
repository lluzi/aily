"""Gating system for Aily information flow.

Hydrological Architecture:
- Rain (inputs): All incoming information
- Streams (channels): Content type routing
- Reservoir (buffer): Accumulation and enrichment
- Dam (gates): Quality/confidence thresholds
- Rivers (outputs): Controlled flow to destinations
"""

from .drainage import DrainageSystem
from .reservoir import ContentReservoir
from .dam import InsightDam
from .channels import InputChannel, OutputChannel

__all__ = [
    "DrainageSystem",
    "ContentReservoir",
    "InsightDam",
    "InputChannel",
    "OutputChannel",
]
