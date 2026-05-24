"""StateMachine geçiş kuralları ve event yayını."""

from __future__ import annotations

import pytest

from src.core.errors import OrchestratorError
from src.core.interfaces import SessionEvent, SessionState
from src.orchestrator.state_machine import StateMachine


@pytest.fixture
def sm() -> StateMachine:
    return StateMachine(session_id="test")


async def test_valid_transition_emits_event(sm: StateMachine) -> None:
    received: list[SessionEvent] = []

    async def listener(event: SessionEvent) -> None:
        received.append(event)

    sm.add_listener(listener)
    await sm.transition_to(SessionState.WELCOME)

    assert sm.state == SessionState.WELCOME
    assert len(received) == 1
    assert received[0].type == "state_changed"
    assert received[0].data["previous"] == "idle"
    assert received[0].data["new"] == "welcome"


async def test_invalid_transition_raises(sm: StateMachine) -> None:
    with pytest.raises(OrchestratorError) as excinfo:
        await sm.transition_to(SessionState.SPEAKING)
    assert excinfo.value.error_code == "ORC_001"
    assert sm.state == SessionState.IDLE


async def test_full_happy_path() -> None:
    sm = StateMachine(session_id="happy")
    expected = [
        SessionState.WELCOME,
        SessionState.LISTENING,
        SessionState.SELECTION,
        SessionState.THINKING,
        SessionState.SPEAKING,
        SessionState.LISTENING,
        SessionState.FAREWELL,
        SessionState.IDLE,
    ]
    for state in expected:
        await sm.transition_to(state)
    assert sm.state == SessionState.IDLE


async def test_listener_removal() -> None:
    sm = StateMachine(session_id="remove")
    received: list[SessionEvent] = []

    async def listener(event: SessionEvent) -> None:
        received.append(event)

    sm.add_listener(listener)
    await sm.transition_to(SessionState.WELCOME)
    sm.remove_listener(listener)
    await sm.transition_to(SessionState.LISTENING)
    assert len(received) == 1
