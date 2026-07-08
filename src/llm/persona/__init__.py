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
from src.llm.persona.nizamulmulk import NIZAMULMULK_PERSONA

# ALPARSLAN ŞİMDİLİK DEAKTİF (kullanıcı isteği). Veri/RAG/persona dosyaları duruyor;
# geri açmak için: aşağıdaki import + PERSONAS satırını + __all__ girdisini + keywords.py
# alparslan bloğunu yorumdan çıkar (persona ve RAG store hazır).
# from src.llm.persona.alparslan import ALPARSLAN_PERSONA

# Asıl proje (Nizamiye Medresesi) karakterleri: GAZÂLÎ + NİZAMÜLMÜLK (ALPARSLAN deaktif).
# Cezerî test/seçim ekranı için tutuluyor (asıl sergide yer almayacak).
PERSONAS: dict[str, PersonaConfig] = {
    GAZALI_PERSONA.id: GAZALI_PERSONA,
    NIZAMULMULK_PERSONA.id: NIZAMULMULK_PERSONA,
    # ALPARSLAN_PERSONA.id: ALPARSLAN_PERSONA,  # DEAKTİF (şimdilik)
    CEZERI_PERSONA.id: CEZERI_PERSONA,
}


def get_persona(persona_id: str) -> PersonaConfig | None:
    return PERSONAS.get(persona_id)


def list_personas() -> list[PersonaConfig]:
    return list(PERSONAS.values())


__all__ = [
    "PERSONAS",
    "get_persona",
    "list_personas",
    # "ALPARSLAN_PERSONA",  # DEAKTİF (şimdilik)
    "CEZERI_PERSONA",
    "GAZALI_PERSONA",
    "NIZAMULMULK_PERSONA",
]
