"""LlamaConfig + persona prompt assembly testleri (model yüklemez)."""

from __future__ import annotations

from datetime import datetime

from src.core.interfaces import ConversationContext, ConversationTurn, PersonaConfig
from src.llm.llama_local import LlamaConfig, LlamaLocalLLM


def _persona() -> PersonaConfig:
    return PersonaConfig(
        id="cezeri",
        name="Cezerî",
        voice_id="cezeri",
        system_prompt="Sen Cezerî'sin, mucitsin.",
        rag_collection="cezeri_responses",
        initial_greeting="Selam evladım.",
    )


def test_llama_config_defaults() -> None:
    cfg = LlamaConfig()
    assert cfg.n_gpu_layers == -1
    assert cfg.n_ctx == 4096
    assert cfg.temperature == 0.6
    assert "<|eot_id|>" in cfg.stop_sequences


def test_llama_config_dict_override() -> None:
    llm = LlamaLocalLLM({"temperature": 0.2, "max_tokens": 100})
    assert llm.config.temperature == 0.2
    assert llm.config.max_tokens == 100


def test_build_messages_system_only() -> None:
    llm = LlamaLocalLLM()
    persona = _persona()
    msgs = llm._build_messages("Robot nedir?", persona, None)
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == persona.system_prompt
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "Robot nedir?"
    assert len(msgs) == 2


def test_build_messages_with_context() -> None:
    llm = LlamaLocalLLM()
    persona = _persona()
    ctx = ConversationContext(
        session_id="abc",
        persona_id="cezeri",
        turns=[
            ConversationTurn(role="visitor", text="Merhaba", timestamp=datetime.utcnow()),
            ConversationTurn(role="persona", text="Aleyküm selam", timestamp=datetime.utcnow()),
        ],
    )
    msgs = llm._build_messages("Robot nedir?", persona, ctx)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "Merhaba"
    assert msgs[2]["content"] == "Aleyküm selam"
    assert msgs[3]["content"] == "Robot nedir?"


def test_build_messages_trims_long_history() -> None:
    llm = LlamaLocalLLM()
    persona = _persona()
    turns = [
        ConversationTurn(role="visitor", text=f"q{i}", timestamp=datetime.utcnow())
        for i in range(10)
    ]
    ctx = ConversationContext(session_id="x", persona_id="cezeri", turns=turns)
    msgs = llm._build_messages("yeni soru", persona, ctx)
    # system + 6 son tur + son user = 8
    assert len(msgs) == 8
    assert msgs[-1]["content"] == "yeni soru"
