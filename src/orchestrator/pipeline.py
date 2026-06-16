"""Tek bir konuşma turunu uçtan uca yürüten pipeline.

Audio → STT → Intent → (Persona seçimi) → Güvenlik → RAG/LLM → Güvenlik filtresi
→ TTS → LipSync → Scene. Tüm provider'lar interface'ler üzerinden çağrılır.

Güvenlik (M1): hassas giriş → LLM atlanır, persona fallback; LLM çıktısı
hoparlöre gitmeden filtreden geçer. Metrikler (M3) her turda enstrümante edilir.
İptal (M9): ``cancel_event`` set edilirse tur erken ve güvenli sonlanır.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator

from src.core.interfaces import (
    BlendshapeFrame,
    ConversationContext,
    DialogueTurn,
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
from src.core.metrics import (
    end_to_end_latency_ms,
    errors_total,
    llm_latency_ms,
    turns_total,
)
from src.llm.persona import get_persona
from src.llm.safety import SafetyFilter
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
        self.cancelled: bool = False


class ConversationPipeline:
    """Provider'ları orkestre eden ana pipeline."""

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
        safety: SafetyFilter | None = None,
    ) -> None:
        self.stt = stt
        self.intent = intent
        self.llm = llm
        self.rag = rag
        self.tts = tts
        self.lipsync = lipsync
        self.scene = scene
        self.rag_threshold = rag_similarity_threshold
        self.safety = safety or SafetyFilter()

    async def run_turn(
        self,
        *,
        session: Session,
        sm: StateMachine,
        audio_stream: AsyncIterator[bytes],
        cancel_event: asyncio.Event | None = None,
    ) -> TurnResult:
        """Bir tam tur: LISTENING → SELECTION/THINKING → SPEAKING → LISTENING."""
        result = TurnResult()
        start = time.perf_counter()
        cancel = cancel_event or asyncio.Event()

        try:
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
            if cancel.is_set():
                return self._finish(result, start, cancelled=True)

            # 2) Intent
            intent = await self.intent.detect(transcription.text, sm.state)
            result.intent = intent
            await sm.emit(
                SessionEvent(
                    session_id=session.session_id,
                    type="intent_detected",
                    data=intent.model_dump(),
                )
            )

            # 3) Persona
            persona_id, persona = self._resolve_persona(intent, session)
            if persona is None:
                await sm.transition_to(SessionState.FAREWELL)
                return self._finish(result, start)
            result.persona_id = persona_id

            first_selection = intent.type == "selection" and session.current_persona != persona_id
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

            # 4) THINKING — Güvenlik + RAG/LLM
            await sm.transition_to(SessionState.THINKING)
            await sm.emit(
                SessionEvent(
                    session_id=session.session_id,
                    type="llm_response_started",
                    data={"persona": persona_id},
                )
            )
            response_text, source = await self._generate_response(
                transcription.text, persona, session, first_selection=first_selection
            )
            result.llm_response = response_text
            result.llm_source = source
            turns_total.labels(persona=persona_id, source=source).inc()
            await sm.emit(
                SessionEvent(
                    session_id=session.session_id,
                    type="llm_response_completed",
                    data={"persona": persona_id, "source": source, "length": len(response_text)},
                )
            )
            if cancel.is_set():
                return self._finish(result, start, cancelled=True)

            # 5) SPEAKING — TTS + LipSync
            await sm.transition_to(SessionState.SPEAKING)
            await self.scene.send_command(
                SceneCommand(
                    command="play_audio",
                    params={"character_id": persona_id},
                    request_id=uuid.uuid4().hex,
                )
            )
            audio_bytes, blendshape_frames = await self._speak(
                response_text, persona, cancel
            )
            result.audio_bytes = audio_bytes
            result.blendshape_frames = blendshape_frames

            # Tur kaydı (zengin session kaydı)
            session.turns.append(
                ConversationTurn(
                    visitor_text=transcription.text,
                    visitor_audio_duration_ms=transcription.duration_ms,
                    detected_intent=intent,
                    selected_persona=persona_id,
                    llm_response=response_text,
                    llm_source=source,  # type: ignore[arg-type]
                )
            )

            if intent.type != "farewell" and not cancel.is_set():
                await sm.transition_to(SessionState.LISTENING)

            return self._finish(result, start, cancelled=cancel.is_set())

        except Exception as exc:  # noqa: BLE001
            errors_total.labels(component="pipeline", error_type=type(exc).__name__).inc()
            _log.exception("pipeline_turn_error", error=str(exc))
            raise

    def _finish(self, result: TurnResult, start: float, cancelled: bool = False) -> TurnResult:
        result.cancelled = cancelled
        result.total_latency_ms = int((time.perf_counter() - start) * 1000)
        # M3: uçtan uca latency metriği (alert'lerin baktığı seri)
        end_to_end_latency_ms.observe(result.total_latency_ms)
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
        # Tek karakter dönemi — varsayılan Cezerî
        default = get_persona("cezeri")
        if default is not None:
            return "cezeri", default
        return None, None

    def _build_context(self, session: Session, persona_id: str) -> ConversationContext:
        """Zengin session.turns → LLM için sade DialogueTurn listesi."""
        ctx = ConversationContext(session_id=session.session_id, persona_id=persona_id)
        for t in session.turns[-3:]:  # son 3 tur (visitor+persona = 6 mesaj)
            if t.visitor_text:
                ctx.turns.append(DialogueTurn(role="visitor", text=t.visitor_text))
            if t.llm_response:
                ctx.turns.append(DialogueTurn(role="persona", text=t.llm_response))
        return ctx

    async def _generate_response(
        self,
        question: str,
        persona: PersonaConfig,
        session: Session,
        *,
        first_selection: bool = False,
    ) -> tuple[str, str]:
        # 0) İlk karakter seçimi anı — selam ver (en kritik ilk temas)
        if first_selection and not question.strip():
            return persona.initial_greeting, "greeting"

        # 1) M1 — Giriş güvenlik sınıflandırması (LLM'i hiç çağırmadan)
        category = self.safety.classify_input(question)
        if category is not None:
            fallback = persona.safety_fallbacks.get(category) or persona.safety_fallbacks.get(
                "inappropriate", "Bunu konuşmayalım evladım."
            )
            return fallback, "fallback"

        # 2) RAG
        try:
            results = await self.rag.query(question, persona.id, top_k=3)
        except Exception as exc:  # noqa: BLE001
            errors_total.labels(component="rag", error_type=type(exc).__name__).inc()
            _log.warning("rag_query_failed", error=str(exc))
            results = []

        if results and results[0].similarity >= self.rag_threshold:
            # Hit → onaylı, güvenli, anlık (filtreye gerek yok)
            return results[0].response_text, "rag"

        # 3) RAG miss → augmented generation: eşik altı en yakınları LLM'e grounding ver
        ctx = self._build_context(session, persona.id)
        ctx.retrieved = [r.response_text for r in results[:2]] if results else []

        llm_start = time.perf_counter()
        chunks: list[str] = []
        try:
            async for token in self.llm.generate_response(question, persona, ctx):
                chunks.append(token)
        except Exception as exc:  # noqa: BLE001
            errors_total.labels(component="llm", error_type=type(exc).__name__).inc()
            _log.warning("llm_generate_failed", error=str(exc))
            return persona.safety_fallbacks.get(
                "unknown_modern", "Şu an cevap veremiyorum evladım, birazdan tekrar dene."
            ), "fallback"
        llm_latency_ms.labels(source="generated").observe(
            int((time.perf_counter() - llm_start) * 1000)
        )

        text = "".join(chunks).strip()

        # 4) M1 — Çıkış güvenlik filtresi (hoparlöre gitmeden)
        verdict = self.safety.check_output(text, persona)
        if not verdict.safe:
            return verdict.text, "fallback"
        return verdict.text, "generated"

    async def _speak(
        self,
        text: str,
        persona: PersonaConfig,
        cancel: asyncio.Event,
    ) -> tuple[int, int]:
        """TTS audio'yu lip-sync'e dallandır ve scene'e gönder.

        ``cancel`` set edilirse üretim erken durur (M9 barge-in / acil kes).
        """
        audio_to_lipsync: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        audio_bytes_total = 0

        async def producer() -> None:
            nonlocal audio_bytes_total
            async for chunk in self.tts.synthesize_stream(text, persona.voice_id):
                if cancel.is_set():
                    break
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
