"""Legacy location shim; the implementation lives in the capability package."""
from library.capabilities.temporal.media_decode import *  # noqa: F403,F401
from library.capabilities.temporal.media_decode import (
    DecodedMedia,
    decode_temporal_media,
)


__all__ = [
    "DecodedMedia",
    "decode_temporal_media",
]
