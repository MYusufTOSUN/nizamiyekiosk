"""Persona registry — yeni karakterler eklenince buraya kaydedilir."""

from __future__ import annotations

from src.core.interfaces import PersonaConfig
from src.llm.persona.cezeri import CEZERI_PERSONA

PERSONAS: dict[str, PersonaConfig] = {
    CEZERI_PERSONA.id: CEZERI_PERSONA,
}


def get_persona(persona_id: str) -> PersonaConfig | None:
    return PERSONAS.get(persona_id)


def list_personas() -> list[PersonaConfig]:
    return list(PERSONAS.values())


__all__ = ["PERSONAS", "get_persona", "list_personas", "CEZERI_PERSONA"]
