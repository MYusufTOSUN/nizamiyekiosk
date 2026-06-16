"""Uçtan uca pipeline testi — sadece mock provider'larla."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from src.core.interfaces import SessionState
from src.intent.detector import KeywordIntentDetector
from src.lipsync.mock import MockLipSync
from src.llm.mock import MockLLM
from src.llm.rag_mock import MockRAGStore
from src.orchestrator.app import app
from src.orchestrator.pipeline import ConversationPipeline
from src.orchestrator.session import Session
from src.orchestrator.state_machine import StateMachine
from src.stt.mock import MockSTT
from src.tts.mock import MockTTS
from src.unreal_bridge.mock import MockSceneController


async def _fake_audio() -> AsyncIterator[bytes]:
    # Sahte 320-sample PCM chunk'ları (640 byte) — 5 tane
    chunk = b"\x00" * 640
    for _ in range(5):
        yield chunk


@pytest.mark.e2e
async def test_pipeline_runs_full_turn_with_mocks() -> None:
    scene = MockSceneController()
    pipeline = ConversationPipeline(
        stt=MockSTT(),
        intent=KeywordIntentDetector(),
        llm=MockLLM(),
        rag=MockRAGStore(),  # düşük similarity → LLM'e düşer
        tts=MockTTS(),
        lipsync=MockLipSync(),
        scene=scene,
        rag_similarity_threshold=0.85,
    )

    session = Session()
    sm = StateMachine(session_id=session.session_id)
    await sm.transition_to(SessionState.WELCOME)  # IDLE → WELCOME

    result = await pipeline.run_turn(
        session=session,
        sm=sm,
        audio_stream=_fake_audio(),
    )

    assert result.transcription is not None
    assert result.intent is not None
    assert result.persona_id == "cezeri"
    assert result.llm_response  # boş olmamalı
    assert result.audio_bytes > 0
    assert result.blendshape_frames > 0
    assert result.total_latency_ms > 0
    # selection intent geldi, bir tur sonrası LISTENING'e döndü
    assert sm.state == SessionState.LISTENING
    # scene komutları (init dışında show_character + play_audio)
    commands = [c.command for c in scene.commands]
    assert "show_character" in commands
    assert "play_audio" in commands


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_characters_endpoint() -> None:
    with TestClient(app) as client:
        r = client.get("/api/v1/characters")
        assert r.status_code == 200
        characters = r.json()
        assert any(c["id"] == "cezeri" for c in characters)


def test_session_lifecycle() -> None:
    with TestClient(app) as client:
        start = client.post("/api/v1/session/start")
        assert start.status_code == 200
        data = start.json()
        assert data["state"] == "welcome"
        assert data["session_id"]

        end = client.post("/api/v1/session/end")
        assert end.status_code == 200
        assert end.json()["ended"] is True


def test_metrics_endpoint() -> None:
    with TestClient(app) as client:
        r = client.get("/api/v1/metrics")
        assert r.status_code == 200
        assert b"bilimfest_" in r.content


@pytest.mark.e2e
def test_ws_audio_full_pipeline() -> None:
    with TestClient(app) as client, client.websocket_connect("/ws/audio") as ws:
        # Birkaç fake PCM chunk gönder
        for _ in range(5):
            ws.send_bytes(b"\x00" * 640)
        ws.send_text("__end__")
        payload = ws.receive_json()

    assert payload["type"] == "turn_completed"
    assert payload["transcription"]
    assert payload["response"]
    assert payload["persona"] == "cezeri"
    assert payload["audio_bytes"] > 0
    assert payload["blendshape_frames"] > 0
