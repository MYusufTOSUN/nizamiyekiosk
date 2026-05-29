"""MockLipSync — audio amplitude'den basit ağız animasyonu üretir.

Gerçek Audio2Face yokken bile makul lipsync görüntüsü için RMS tabanlı
jawOpen + alternating mouthFunnel/mouthPucker. Phase 7 Unreal entegrasyonu
test edilirken bu blendshape stream'i geçerli bir kaynak olur.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np

from src.core.interfaces import BlendshapeFrame, LipSyncProvider
from src.stt.audio_utils import pcm_bytes_to_numpy, rms

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

# Mock lipsync chunk başına 20 ms @ 24 kHz varsayar; gerçek TTS çıkışı bu format.
DEFAULT_CHUNK_MS = 20
JAW_RMS_GAIN = 8.0          # RMS [0,~0.1] → jawOpen [0,1] yaklaşık doğrusal
BLINK_INTERVAL_MS = 3000    # 3 sn'de bir kısa göz kırpma
BLINK_DURATION_MS = 120


class MockLipSync(LipSyncProvider):
    """Audio RMS'inden jawOpen + periyodik göz kırpma üreten basit lipsync."""

    async def generate_blendshapes(
        self,
        audio_stream: AsyncIterator[bytes],
        character_id: str,
    ) -> AsyncIterator[BlendshapeFrame]:
        timestamp_ms = 0
        async for chunk in audio_stream:
            if not chunk:
                continue
            audio = pcm_bytes_to_numpy(chunk)
            level = rms(audio)
            jaw = float(min(1.0, max(0.0, level * JAW_RMS_GAIN)))

            # Sınırlı dalga karışımı — ağız sallanmasını biraz çeşitlendir
            funnel = jaw * 0.5 * (1.0 + np.sin(timestamp_ms / 90.0))
            pucker = jaw * 0.3 * (1.0 + np.cos(timestamp_ms / 110.0))

            # Periyodik göz kırpma
            phase = timestamp_ms % BLINK_INTERVAL_MS
            blink_value = 1.0 if phase < BLINK_DURATION_MS else 0.0

            values = {k: 0.0 for k in ARKIT_BLENDSHAPE_KEYS}
            values["jawOpen"] = jaw
            values["mouthFunnel"] = float(max(0.0, min(1.0, funnel)))
            values["mouthPucker"] = float(max(0.0, min(1.0, pucker)))
            values["eyeBlinkLeft"] = blink_value
            values["eyeBlinkRight"] = blink_value

            yield BlendshapeFrame(timestamp_ms=timestamp_ms, values=values)
            timestamp_ms += DEFAULT_CHUNK_MS
