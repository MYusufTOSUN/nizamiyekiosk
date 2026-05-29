"""Unreal bridge binary packet protokolü.

`api_contracts.md §7.2` (UDP blendshape stream) ve §7.3 (TCP JSON command)
ile birebir uyumlu. Header magic ``b"BFST"``. Sabitler ve struct format string'leri
burada toplu — değişirse hem Python hem Unreal tarafı tek noktadan güncellenir.
"""

from __future__ import annotations

import struct
from typing import Final

MAGIC: Final[bytes] = b"BFST"

# Mesaj türleri (header msg_type alanı)
MSG_BLENDSHAPE: Final[int] = 0x01
MSG_HEARTBEAT: Final[int] = 0x02

# Header: 4-byte magic + 1-byte msg_type + 1-byte reserved + 2-byte payload_length LE
HEADER_FMT: Final[str] = "<4sBBH"
HEADER_SIZE: Final[int] = struct.calcsize(HEADER_FMT)

# Blendshape payload: 8-byte timestamp_us + 16-byte char_id + 1-byte count + 52*float32
BLENDSHAPE_PAYLOAD_FMT: Final[str] = "<q16sB52f"
BLENDSHAPE_PAYLOAD_SIZE: Final[int] = struct.calcsize(BLENDSHAPE_PAYLOAD_FMT)

ARKIT_BLENDSHAPE_KEYS: Final[tuple[str, ...]] = (
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
)
assert len(ARKIT_BLENDSHAPE_KEYS) == 52


def pack_blendshape_packet(
    timestamp_us: int,
    character_id: str,
    values: dict[str, float],
) -> bytes:
    """Tek bir UDP paketi inşa et."""
    char_bytes = character_id.encode("ascii", errors="replace")[:16].ljust(16, b"\x00")
    floats = [float(values.get(k, 0.0)) for k in ARKIT_BLENDSHAPE_KEYS]
    payload = struct.pack(
        BLENDSHAPE_PAYLOAD_FMT, timestamp_us, char_bytes, 52, *floats
    )
    header = struct.pack(HEADER_FMT, MAGIC, MSG_BLENDSHAPE, 0x00, len(payload))
    return header + payload


def unpack_blendshape_packet(packet: bytes) -> tuple[int, str, dict[str, float]]:
    """Bir UDP paketini çöz. Test ve simülatör için."""
    if len(packet) < HEADER_SIZE + BLENDSHAPE_PAYLOAD_SIZE:
        raise ValueError(f"Paket çok küçük: {len(packet)}")
    magic, msg_type, _reserved, payload_len = struct.unpack(
        HEADER_FMT, packet[:HEADER_SIZE]
    )
    if magic != MAGIC:
        raise ValueError(f"Geçersiz magic: {magic!r}")
    if msg_type != MSG_BLENDSHAPE:
        raise ValueError(f"Bu blendshape değil: msg_type=0x{msg_type:02x}")
    payload = packet[HEADER_SIZE : HEADER_SIZE + payload_len]
    if len(payload) != BLENDSHAPE_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload boyut hatası: beklenen {BLENDSHAPE_PAYLOAD_SIZE}, bulunan {len(payload)}"
        )
    fields = struct.unpack(BLENDSHAPE_PAYLOAD_FMT, payload)
    timestamp_us = int(fields[0])
    character_id = fields[1].rstrip(b"\x00").decode("ascii", errors="replace")
    floats = fields[3:]
    values = {k: float(v) for k, v in zip(ARKIT_BLENDSHAPE_KEYS, floats, strict=True)}
    return timestamp_us, character_id, values


__all__ = [
    "MAGIC",
    "MSG_BLENDSHAPE",
    "MSG_HEARTBEAT",
    "HEADER_FMT",
    "HEADER_SIZE",
    "BLENDSHAPE_PAYLOAD_FMT",
    "BLENDSHAPE_PAYLOAD_SIZE",
    "ARKIT_BLENDSHAPE_KEYS",
    "pack_blendshape_packet",
    "unpack_blendshape_packet",
]
