"""Persona registry — yeni karakterler eklenince buraya kaydedilir.

Yeni karakter eklemek için:
    python scripts/new_character.py <id> --name "Ad" --era "yıllar"
ardından üretilen persona dosyasını doldurup import edip aşağıdaki PERSONAS
dict'ine ekle. (Diğer 4 karakter kullanıcı tarafından henüz belirlenmedi.)
"""

from __future__ import annotations

from src.core.interfaces import PersonaConfig
from src.llm.persona.cezeri import CEZERI_PERSONA
from src.llm.persona.gazali import GAZALI_PERSONA

# Asıl proje (Nizamiye Medresesi) karakteri: GAZÂLÎ. Cezerî test/seçim ekranı
# için tutuluyor (asıl sergide yer almayacak).
PERSONAS: dict[str, PersonaConfig] = {
    GAZALI_PERSONA.id: GAZALI_PERSONA,
    CEZERI_PERSONA.id: CEZERI_PERSONA,
}


def get_persona(persona_id: str) -> PersonaConfig | None:
    return PERSONAS.get(persona_id)


def list_personas() -> list[PersonaConfig]:
    return list(PERSONAS.values())


__all__ = ["PERSONAS", "get_persona", "list_personas", "CEZERI_PERSONA", "GAZALI_PERSONA"]
