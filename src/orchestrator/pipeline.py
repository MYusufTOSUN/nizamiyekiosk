"""Tek bir konuşma turunu uçtan uca yürüten pipeline.

Audio → STT → Intent → (Persona seçimi) → LLM (RAG fallback) → TTS → LipSync
→ Scene. Tüm provider'lar interface'ler üzerinden çağrılır — gerçek/mock farkı
yok.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

from src.core.interfaces import (
    BlendshapeFrame,
    Intent,
    IntentDetector,
    LLMProvider,
    LipSyncProvider,
    PersonaConfig,
    RAGStore,
    SceneCommand,
    SceneController,
    SessionEvent,
    SessionState,
    STTProvider,
    TranscriptionResult,
    TTSProvider,
)
from src.core.logger import get_logger
from src.llm.persona import get_persona
from src.orchestrator.session import ConversationTurn, Session
from src.orchestrator.state_machine import StateMachine

_log = get_logger(component="orchestrator.pipeline")


class TurnResult:
    """Bir konuşma turunun sonucunda biriken bilgi."""

    def __init__(self) -> None:
        self.transcription: TranscriptionResult | None = None
        self.intent: Intent | None = None
        self.persona_id: str | None = None
        self.llm_response: str = ""
        self.llm_source: str = "generated"
        self.audio_bytes: int = 0
        self.blendshape_frames: int = 0
        self.total_latency_ms: int = 0


class ConversationPipeline:
    """Provider'ları orkestre eden ana pipeline.

    Phase 1'de WebSocket audio → mock STT → ... → mock SceneController zinciri
    çalışır.
    """

    def __init__(
        self,
        *,
        stt: STTProvider,
        intent: IntentDetector,
        llm: LLMProvider,
        rag: RAGStore,
        tts: TTSProvider,
        lipsync: LipSyncProvider,
        scene: SceneController,
        rag_similarity_threshold: float = 0.85,
    ) -> None:
        self.stt = stt
        self.intent = intent
        self.llm = llm
        self.rag = rag
        self.tts = tts
        self.lipsync = lipsync
        self.scene = scene
        self.rag_threshold = rag_similarity_threshold

    async def run_turn(
        self,
        *,
        session: Session,
        sm: StateMachine,
        audio_stream: AsyncIterator[bytes],
    ) -> TurnResult:
        """Bir tam tur: LISTENING → SELECTION/THINKING → SPEAKING → LISTENING."""
        result = TurnResult()
        start = time.perf_counter()

        # 1) LISTENING — STT
        await sm.transition_to(SessionState.LISTENING)
        transcription = await self._first_final_transcription(audio_stream)
        result.transcription = transcription
        await sm.emit(
            SessionEvent(
                session_id=session.session_id,
                type="transcription_received",
                data={"text": transcription.text, "confidence": transcription.confidence},
            )
        )

        # 2) Intent detection
        intent = await self.intent.detect(transcription.text, sm.state)
        result.intent = intent
        await sm.emit(
            SessionEvent(
                session_id=session.session_id,
                type="intent_detected",
                data=intent.model_dump(),
            )
        )

        # 3) Persona seçimi veya mevcut persona
        persona_id, persona = self._resolve_persona(intent, session)
        if persona is None:
            # Karakter seçilmemiş ve niyet de selection değil — vedaya geç.
            await sm.transition_to(SessionState.FAREWELL)
            result.total_latency_ms = int((time.perf_counter() - start) * 1000)
            return result

        result.persona_id = persona_id
        if intent.type == "selection":
            await sm.transition_to(SessionState.SELECTION)
            session.current_persona = persona_id
            await self.scene.send_command(
                SceneCommand(
                    command="show_character",
                    params={"character_id": persona_id, "transition": "fade_in"},
                    request_id=uuid.uuid4().hex,
                )
            )

        # 4) THINKING — LLM (RAG fallback)
        await sm.transition_to(SessionState.THINKING)
        await sm.emit(
            SessionEvent(
                session_id=session.session_id,
                type="llm_response_started",
                data={"persona": persona_id},
            )
        )
        response_text, source = await self._generate_response(transcription.text, persona)
        result.llm_response = response_text
        result.llm_source = source
        await sm.emit(
            SessionEvent(
                session_id=session.session_id,
                type="llm_response_completed",
                data={"persona": persona_id, "source": source, "length": len(response_text)},
            )
        )

        # 5) SPEAKING — TTS + LipSync paralel
        await sm.transition_to(SessionState.SPEAKING)
        await self.scene.send_command(
            SceneCommand(
                command="play_audio",
                params={"character_id": persona_id},
                request_id=uuid.uuid4().hex,
            )
        )
        audio_bytes, blendshape_frames = await self._speak(response_text, persona)
        result.audio_bytes = audio_bytes
        result.blendshape_frames = blendshape_frames

        # Turun kaydı
        turn = ConversationTurn(
            visitor_text=transcription.text,
            visitor_audio_duration_ms=transcription.duration_ms,
            detected_intent=intent,
            selected_persona=persona_id,
            llm_response=response_text,
            llm_source=source,  # type: ignore[arg-type]
        )
        session.turns.append(turn)

        # 6) LISTENING'e dön — sohbet devam edebilsin
        if intent.type != "farewell":
            await sm.transition_to(SessionState.LISTENING)

        result.total_latency_ms = int((time.perf_counter() - start) * 1000)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _first_final_transcription(
        self,
        audio_stream: AsyncIterator[bytes],
    ) -> TranscriptionResult:
        last: TranscriptionResult | None = None
        async for chunk in self.stt.transcribe_stream(audio_stream):
            last = chunk
            if chunk.is_final:
                return chunk
        if last is None:
            # Boş stream — boş transcription dönelim
            return TranscriptionResult(text="", is_final=True, confidence=0.0, duration_ms=0)
        return last

    def _resolve_persona(
        self,
        intent: Intent,
        session: Session,
    ) -> tuple[str | None, PersonaConfig | None]:
        if intent.type == "selection" and intent.target:
            persona = get_persona(intent.target)
            if persona is not None:
                return intent.target, persona

        if session.current_persona:
            persona = get_persona(session.current_persona)
            if persona is not None:
                return session.current_persona, persona

        # Phase 1: tek karakter — varsayılan Cezerî
        default = get_persona("cezeri")
        if default is not None:
            return "cezeri", default

        return None, None

    async def _generate_response(
        self,
        question: str,
        persona: PersonaConfig,
    ) -> tuple[str, str]:
        # 1) RAG dene
        try:
            results = await self.rag.query(question, persona.id, top_k=3)
        except Exception as exc:  # pragma: no cover — RAG mock güvenli
            _log.warning("rag_query_failed", error=str(exc))
            results = []

        if results and results[0].similarity >= self.rag_threshold:
            return results[0].response_text, "rag"

        # 2) LLM streaming → string birleştir
        chunks: list[str] = []
        async for token in self.llm.generate_response(question, persona):
            chunks.append(token)
        text = "".join(chunks).strip()
        if not text:
            # 3) Fallback
            return persona.safety_fallbacks.get(
                "unknown_modern",
                "Şu an cevap veremiyorum evladım, birazdan tekrar dene.",
            ), "fallback"
        return text, "generated"

    async def _speak(
        self,
        text: str,
        persona: PersonaConfig,
    ) -> tuple[int, int]:
        """TTS audio'yu lip-sync'e dallandır ve scene'e gönder.

        Tek tüketici problemini çözmek için audio'yu iki kuyruğa yazıyoruz:
        biri lipsync için (blendshape üretir), diğeri buffer ölçümü için.
        """
        audio_to_lipsync: asyncio.Queue[bytes | None] = asyncio.Queue()
        audio_bytes_total = 0

        async def producer() -> None:
            nonlocal audio_bytes_total
            async for chunk in self.tts.synthesize_stream(text, persona.voice_id):
                audio_bytes_total += len(chunk)
                await audio_to_lipsync.put(chunk)
            await audio_to_lipsync.put(None)  # EOS

        async def lipsync_iter() -> AsyncIterator[bytes]:
            while True:
                chunk = await audio_to_lipsync.get()
                if chunk is None:
                    return
                yield chunk

        blendshape_count = 0

        async def lipsync_consumer() -> None:
            nonlocal blendshape_count
            frames = self.lipsync.generate_blendshapes(lipsync_iter(), persona.id)
            buffer: list[BlendshapeFrame] = []

            async def collect() -> AsyncIterator[BlendshapeFrame]:
                async for frame in frames:
                    buffer.append(frame)
                    yield frame

            await self.scene.send_blendshapes(persona.id, collect())
            blendshape_count = len(buffer)

        await asyncio.gather(producer(), lipsync_consumer())
        return audio_bytes_total, blendshape_count


__all__ = ["ConversationPipeline", "TurnResult"]
