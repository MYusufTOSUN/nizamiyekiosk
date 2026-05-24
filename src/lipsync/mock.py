"""MockLipSync — audio stream'i tüketir, sıfırlı blendshape frame'leri üretir."""

from __future__ import annotations

from collections.abc import AsyncIterator

from src.core.interfaces import BlendshapeFrame, LipSyncProvider

ARKIT_BLENDSHAPE_KEYS: list[str] = [
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight",
    "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper",
    "mouthPressLeft", "mouthPressRight", "mouthLowerDownLeft", "mouthLowerDownRight",
    "mouthUpperUpLeft", "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft", "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
]
assert len(ARKIT_BLENDSHAPE_KEYS) == 52


class MockLipSync(LipSyncProvider):
    """Audio chunk başına 1 sıfırlı BlendshapeFrame yayınlar."""

    async def generate_blendshapes(
        self,
        audio_stream: AsyncIterator[bytes],
        character_id: str,
    ) -> AsyncIterator[BlendshapeFrame]:
        timestamp_ms = 0
        zero_values = {k: 0.0 for k in ARKIT_BLENDSHAPE_KEYS}
        async for chunk in audio_stream:
            if not chunk:
                continue
            yield BlendshapeFrame(timestamp_ms=timestamp_ms, values=dict(zero_values))
            timestamp_ms += 20  # her chunk 20 ms (20 ms @ 24 kHz)
