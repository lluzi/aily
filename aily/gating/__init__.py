"""Input drop model for Aily.

Only the durable ``RainDrop`` intake model remains from the original
hydrological design; the reservoir/dam/channel flow machinery was never
wired into the live runtime and has been removed.
"""

from .drainage import DrainageSystem, RainDrop, RainType, Stream, StreamType

__all__ = [
    "DrainageSystem",
    "RainDrop",
    "RainType",
    "Stream",
    "StreamType",
]
