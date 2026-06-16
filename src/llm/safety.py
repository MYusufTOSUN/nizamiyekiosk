"""İçerik güvenlik katmanı — çocuk izleyicili sergi için son güvenlik ağı.

İki yönlü:
1. **Giriş sınıflandırma** (`classify_input`): ziyaretçi sorusu hassas bir
   kategoriye (din/siyaset/uygunsuz) düşüyorsa, LLM'i hiç çağırmadan persona'nın
   önceden onaylı `safety_fallbacks` cevabını döndürmek için kategori anahtarı verir.
2. **Çıkış filtresi** (`check_output`): LLM'in ürettiği metni hoparlöre gitmeden
   önce küfür/argo ve meta-AI sızıntısı ("ben bir yapay zekayım", "dil modeli",
   "GPT/Claude") için tarar; tetiklenirse güvenli fallback'e çevirir.

Bu deterministik bir savunmadır — LLM'in persona kurallarına uymasına GÜVENMEZ.
Türkçe-bilinçli normalizasyon kullanır.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.interfaces import PersonaConfig
from src.core.logger import get_logger
from src.intent.detector import turkish_lower

_log = get_logger(component="llm.safety")


# --- Kategori → tetikleyici kelimeler (giriş sınıflandırma) ----------------
# Conservative tutuldu: bilim sorularını yanlış pozitif yapmamak için yalnız
# açıkça hassas terimler. Kelime-sınırı eşleşmesi.

RELIGION_KEYWORDS: frozenset[str] = frozenset({
    "cehennem", "cennet", "günah", "kafir", "ateist", "tanrı var mı",
    "hangi din", "din doğru", "namaz nasıl", " orucu", "haram mı", "helal mi",
    "peygamber mi", "mezhep", "şeytan",
})

POLITICS_KEYWORDS: frozenset[str] = frozenset({
    "cumhurbaşkanı", "başbakan", "seçim", "parti", "oy ver", "siyaset",
    "hükümet", "muhalefet", "erdoğan", "kemal", "milletvekili", "bakan kim",
    "kürt sorunu", "terör", "darbe",
})

# Türkçe küfür/argo — savunma amaçlı, kısa ve yaygın kökler (kelime sınırıyla).
PROFANITY_ROOTS: frozenset[str] = frozenset({
    "amk", "aq", "oç", "piç", "orospu", "yavşak", "siktir", "sik", "göt",
    "amına", "ananı", "avradını", "pezevenk", "ibne", "gavat", "salak",
    "gerizekalı", "aptal", "mal mısın",
})

# Meta-AI sızıntısı (çıkış filtresi) — karakter kırılması işaretleri.
META_AI_PATTERNS: tuple[str, ...] = (
    "yapay zeka",
    "yapay zekay",
    "dil modeli",
    "dil modeliy",
    "bir model",
    "openai",
    "anthropic",
    "chatgpt",
    "gpt-",
    "claude",
    "llama",
    "language model",
    "as an ai",
    "ben bir asistan",
    "yapay bir",
    "eğitildim",
    "sistem prompt",
)


def _norm(text: str) -> str:
    return turkish_lower(text.strip())


def _has_word(haystack: str, needle: str) -> bool:
    if " " in needle:
        return needle in haystack
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack) is not None


@dataclass
class OutputVerdict:
    safe: bool
    text: str  # güvenli metin (gerekirse fallback ile değiştirilmiş)
    reason: str = ""


class SafetyFilter:
    """Giriş sınıflandırma + çıkış filtresi."""

    def classify_input(self, text: str) -> str | None:
        """Hassas kategori varsa fallback anahtarı döndür, yoksa None.

        Döndürülen anahtar persona.safety_fallbacks'teki bir key'dir:
        'religion' | 'politics' | 'inappropriate'.
        """
        n = _norm(text)
        if not n:
            return None
        for kw in PROFANITY_ROOTS:
            if _has_word(n, kw):
                _log.info("safety_input_inappropriate", kw=kw)
                return "inappropriate"
        for kw in RELIGION_KEYWORDS:
            if _has_word(n, kw):
                _log.info("safety_input_religion", kw=kw)
                return "religion"
        for kw in POLITICS_KEYWORDS:
            if _has_word(n, kw):
                _log.info("safety_input_politics", kw=kw)
                return "politics"
        return None

    def check_output(self, text: str, persona: PersonaConfig) -> OutputVerdict:
        """LLM çıktısını tara; güvensizse persona fallback'ine çevir."""
        n = _norm(text)
        if not n:
            return OutputVerdict(
                safe=False,
                text=self._fallback(persona, "unknown_modern"),
                reason="empty",
            )

        for kw in PROFANITY_ROOTS:
            if _has_word(n, kw):
                _log.warning("safety_output_profanity", kw=kw)
                return OutputVerdict(
                    safe=False, text=self._fallback(persona, "inappropriate"), reason="profanity"
                )

        for pat in META_AI_PATTERNS:
            if pat in n:
                _log.warning("safety_output_meta_ai", pattern=pat)
                return OutputVerdict(
                    safe=False,
                    text=self._fallback(persona, "unknown_modern"),
                    reason="meta_ai_leak",
                )

        return OutputVerdict(safe=True, text=text)

    @staticmethod
    def _fallback(persona: PersonaConfig, key: str) -> str:
        fb = persona.safety_fallbacks
        return fb.get(key) or fb.get("unknown_modern") or (
            "Bunu konuşmayalım evladım, sana atölyemden bir şey anlatayım mı?"
        )


__all__ = ["SafetyFilter", "OutputVerdict"]
