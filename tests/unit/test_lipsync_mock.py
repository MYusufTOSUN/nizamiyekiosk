"""İyileştirilmiş MockLipSync — RMS tabanlı jawOpen + göz kırpma testleri."""

from __future__ import annotations

from collections.abc import AsyncIterator

import numpy as np
import pytest

from src.core.interfaces import BlendshapeFrame
from src.lipsync.mock import ARKIT_BLENDSHAPE_KEYS, MockLipSync


async def _async_iter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for c in chunks:
        yield c


def _to_pcm_chunk(amplitude: float, samples: int = 480) -> bytes:
    # 24 kHz @ 20 ms = 480 sample. Sin wave with given amplitude.
    t = np.arange(samples, dtype=np.float32) / 24000.0
    audio = (amplitude * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()


@pytest.fixture
def ls() -> MockLipSync:
    return MockLipSync()


async def test_silent_audio_gives_closed_mouth(ls: MockLipSync) -> None:
    chunks = [b"\x00" * 960 for _ in range(5)]  # 5 frame of silence
    frames: list[BlendshapeFrame] = []
    async for frame in ls.generate_blendshapes(_async_iter(chunks), "cezeri"):
        frames.append(frame)
    assert len(frames) == 5
    for f in frames:
        assert f.values["jawOpen"] == 0.0
        assert f.values["mouthFunnel"] == 0.0


async def test_loud_audio_opens_jaw(ls: MockLipSync) -> None:
    chunks = [_to_pcm_chunk(0.8) for _ in range(3)]
    frames = []
    async for frame in ls.generate_blendshapes(_async_iter(chunks), "cezeri"):
        frames.append(frame)
    # 0.8 amplitude sine wave -> RMS ≈ 0.566 * 8.0 gain -> clipped to 1.0
    assert all(f.values["jawOpen"] >= 0.5 for f in frames)
    assert max(f.values["jawOpen"] for f in frames) <= 1.0


async def test_frame_keys_are_complete_arkit_set(ls: MockLipSync) -> None:
    chunks = [_to_pcm_chunk(0.3)]
    frames = []
    async for frame in ls.generate_blendshapes(_async_iter(chunks), "cezeri"):
        frames.append(frame)
    assert len(frames) == 1
    assert set(frames[0].values.keys()) == set(ARKIT_BLENDSHAPE_KEYS)
    assert len(frames[0].values) == 52


async def test_timestamp_monotonic_20ms(ls: MockLipSync) -> None:
    chunks = [_to_pcm_chunk(0.5) for _ in range(4)]
    frames = []
    async for frame in ls.generate_blendshapes(_async_iter(chunks), "cezeri"):
        frames.append(frame)
    timestamps = [f.timestamp_ms for f in frames]
    assert timestamps == [0, 20, 40, 60]


async def test_empty_chunks_skipped(ls: MockLipSync) -> None:
    chunks = [_to_pcm_chunk(0.5), b"", _to_pcm_chunk(0.5)]
    frames = []
    async for frame in ls.generate_blendshapes(_async_iter(chunks), "cezeri"):
        frames.append(frame)
    assert len(frames) == 2  # boş chunk atlanır
