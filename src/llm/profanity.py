"""Kapsamlı küfür/kaba-söz tespiti — çocuk sergisi içerik-moderasyonu.

Açık-vermeyen savunma: (1) evazyon-dayanıklı KANONİKLEŞTİRME (leetspeak, harf-arası
boşluk/noktalama, tekrar-harf), (2) tek-kelime kökler için kelime-sınırlı tek
alternasyon regex, çok-ayırt-edici/çok-kelime kalıplar için substring, (3) ham +
kanonik metnin İKİSİNİ tara (biri kaçarsa diğeri yakalar), (4) geçmişe/ekrana
yazmadan maskeleme (LLM kaba sözü grounding'den tekrarlamasın).

Listeler adversarial analiz + yanlış-pozitif küratörlüğüyle üretildi; meşru içeriği
(hayvan adları, 'klasik/müzik', teknik 'yalama vida', 'alçak basınç', 'mal varlığı',
'çark döner') BOZMAYACAK şekilde riskli/yaygın kökler (am, göt, top, meme, hıyar,
avrat, mal, döner...) ELENDİ.
"""
from __future__ import annotations

import re

from src.intent.detector import turkish_lower

# Tek-kelime kökler — KELİME-SINIRIYLA aranır (bitişik kelimede yanlış-pozitif yok).
_WB_TERMS: tuple[str, ...] = (
    'ahmak', 'ahmaksın', 'ahmakça', 'amcik',
    'amcık', 'amcığı', 'amcığını', 'amk',
    'amq', 'amına', 'amını', 'anan',
    'anana', 'ananı', 'ananın', 'anasını',
    'andaval', 'aptal', 'aptala', 'aptalca',
    'aptallar', 'aptalsin', 'aptalsın', 'aptalsınız',
    'aqı', 'avanak', 'avradini', 'avradını',
    'bacini', 'bacını', 'beyinsiz', 'beyinsizsin',
    'bok', 'boka', 'boklar', 'boklu',
    'bokluk', 'boktan', 'boktanlık', 'boku',
    'bokum', 'bokunda', 'bokunu', 'bokça',
    'dalyarak', 'dalyarrak', 'dangalak', 'dangalaksın',
    'dangalağa', 'deli', 'dişlek', 'domuz',
    'döl', 'dışkı', 'embesil', 'ezik',
    'eziksin', 'eşşek', 'fahise', 'fahişe',
    'gavat', 'gavur', 'gerizekali', 'gerizekalı',
    'gerizekalısın', 'gerzek', 'gerzeksin', 'gerzo',
    'godoş', 'gotveren', 'göt', 'göte',
    'götlek', 'götoş', 'götveren', 'götüm',
    'götün', 'götünden', 'götüne', 'götünü',
    'gıcık', 'hasiktir', 'hassikeyim', 'hassiktir',
    'haysiyetsiz', 'hödük', 'ibine', 'ibne',
    'ibneler', 'ibnelik', 'işedi', 'kafir',
    'kahpe', 'kahpelik', 'kahpenin', 'kakamı',
    'kakanı', 'kakasını', 'kaltak', 'kavat',
    'kodumun', 'koduğumun', 'lubunya', 'malsın',
    'mankafa', 'mankafalı', 'manyak', 'moron',
    'namussuz', 'nonoş', 'orospu', 'orospular',
    'orospunun', 'oruspu', 'osurdu', 'osurgan',
    'osurmak', 'osuruyor', 'pezevengin', 'pezevenk',
    'pezo', 'pic', 'pislik', 'piç',
    'piçi', 'piçler', 'piçlik', 'piçsin',
    'psikopat', 'pust', 'puşt', 'puştun',
    'salak', 'salaklar', 'salaksin', 'salaksın',
    'salaksınız', 'salakça', 'salağa', 'salağı',
    'serefsiz', 'sersefil', 'sersem', 'sicarim',
    'sik', 'sikerim', 'sikerler', 'sikeyim',
    'sikik', 'sikim', 'sikimde', 'sikiş',
    'sikiştir', 'sikko', 'sikmek', 'sikti',
    'siktim', 'siktir', 'siktiğim', 'siktiğimin',
    'siktın', 'suratsız', 'surtuk', 'sülaleni',
    'sürtük', 'sıllık', 'sıç', 'sıçacağım',
    'sıçarım', 'sıçayım', 'sıçmak', 'sıçmış',
    'sıçtık', 'sıçtım', 'sıçtın', 'sıçtığım',
    'sıçık', 'tasak', 'taşak', 'taşşak',
    'travesti', 'ucube', 'yamyam', 'yarak',
    'yarağım', 'yarrak', 'yarrağı', 'yavsak',
    'yavşak', 'yavşaksın', 'yavşağı', 'zenci',
    'çingene', 'çirkinsin', 'çişini', 'çüt',
    'şapşal', 'şapşalsın', 'şerefsiz', 'şerefsizin',
    'şerefsizsin', 'şişko', 'şişkosun', 'şıllık',
)

# Çok ayırt edici + çok-kelime sövgüler — SUBSTRING (ek/çekim farketmez).
# NOT: masum kelimeye gömülü kısa kökler (amına->anlamına) buradan çıkarıldı.
_SUB_TERMS: tuple[str, ...] = (
    'a m k', 'alçak herif', 'alçak ırk', 'amina koyim',
    'aminako', 'amına kodum', 'amına koy', 'amına koyayım',
    'amına koyayım seni', 'amına koydu', 'amına koyduğum', 'amına koyduğumun',
    'amına koyim', 'amına koyim seni', 'amınako', 'amınakoyiim',
    'amınakoyim', 'ananı avradını', 'ananı belleyim', 'ananı sikeyim',
    'ananın amı', 'ananısikeyim', 'anasını sikeyim', 'asshole',
    'avradını', 'ağzına sıçayım', 'ağzına sıçtığım', 'aşağı ırk',
    'bastard', 'bitch', 'bok gibi', 'bok herif',
    'bok ye', 'bok yedi', 'bok yedin', 'boktan herif',
    'boktan şey', 'boku çıktı', 'cunt', 'dick',
    'fck', 'fuck', 'geri kafalı', 'geri zekalı',
    'geri zekalılar', 'geri zekalısın', 'geri zekâlı', 'göt veren',
    'götlalesi', 'götlek', 'götoş', 'götver',
    'götveren', 'götüne sokayım', 'halt yedin', 'hayvan herif',
    'hayvan mısın', 'ibne', 'kuş beyinli', 'köstebek surat',
    'mal gibi', 'mal mısın', 'motherf', 'orospu',
    'orospu evladı', 'orospu çocugu', 'orospu çocuğu', 'orspu',
    'osuruk', 'oçocuğu', 'pezeven', 'pezevenk',
    'pis kürt', 'pis türk', 'pis çingene', 'pussy',
    's*kerim', 's.kerim', 'salyangoz kafalı', 'shit',
    'sikeceğim', 'sikerim', 'sikeyim', 'sikici',
    'sikim sonik', 'sikiş', 'sikko', 'sikko herif',
    'siksok', 'siktir', 'siktir et', 'siktir git',
    'siktiğim', 'sktr', 'soyunu sikeyim', 'taşşak',
    'wtf', 'yarağ', 'yarrak', 'yarrrak',
    'yarım akıllı', 'yavşak', 'zekâ özürlü', 'çirkin surat',
)


_LEET = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t",
    "@": "a", "$": "s", "8": "b", "9": "g",
})
_WB_RE = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(t) for t in _WB_TERMS) + r")(?!\w)")


def _norm(text: str) -> str:
    return turkish_lower((text or "").strip())


def canon(text: str) -> str:
    """Evazyon-dayanıklı kanonik biçim (yalnız TARAMA için): leetspeak + harf-arası
    ayraç/boşluk sökme ('b o k'/'b.o.k' -> 'bok') + tekrar-harf daraltma."""
    t = _norm(text).translate(_LEET)
    t = re.sub(r"\b(\w(?:[ .\-_*·]\w){2,})\b",
               lambda m: re.sub(r"[ .\-_*·]", "", m.group(1)), t)
    t = re.sub(r"(?<=\w)[.\-_*·](?=\w)", "", t)
    t = re.sub(r"(.)\1{2,}", r"\1", t)
    return t


def _scan(s: str) -> str | None:
    m = _WB_RE.search(s)
    if m:
        return m.group(0)
    for w in _SUB_TERMS:
        if w in s:
            return w
    return None


def find(text: str) -> str | None:
    """Kaba söz varsa eşleşen kök; önce ham, sonra kanonik metin. None=temiz."""
    if not text:
        return None
    n = _norm(text)
    hit = _scan(n)
    if hit:
        return hit
    c = canon(text)
    return _scan(c) if c != n else None


def contains(text: str) -> bool:
    return find(text) is not None


def sanitize_for_history(text: str) -> str:
    """Geçmişe/ekrana yazmadan ÖNCE kaba sözü nötr yer-tutucuyla değiştir."""
    return "[ziyaretçi uygunsuz bir şey söyledi]" if contains(text) else text


__all__ = ["canon", "find", "contains", "sanitize_for_history"]
