"""İçerik güvenlik katmanı — çocuk izleyicili sergi için son güvenlik ağı.

İki yönlü:
1. **Giriş sınıflandırma** (`classify_input`): ziyaretçi sorusu hassas/tehlikeli bir
   kategoriye düşüyorsa, LLM'i hiç çağırmadan persona'nın önceden onaylı
   `safety_fallbacks` cevabını döndürmek için kategori anahtarı verir.
2. **Çıkış filtresi** (`check_output`): LLM'in ürettiği metni hoparlöre gitmeden
   önce küfür/argo (Türkçe + yabancı), hedefli hakaret ve meta-AI/kimlik sızıntısı
   için tarar; tetiklenirse güvenli fallback'e çevirir.

Bu deterministik bir savunmadır — LLM'in persona kurallarına uymasına GÜVENMEZ.
Türkçe-bilinçli normalizasyon kullanır.

ÖNCELİK SIRASI (classify_input bu sırayla kontrol eder — kritik):
  1) stranger_danger / distress  → her zaman ÖNCE (anlık kaçırma, intihar, istismar,
     ihmal, kayıp). "ölmek istiyorum" gibi ifade self_worth kelimesi de taşısa
     daima distress kazanır.
  2) self_worth                  → küfür kontrolünden ÖNCE ("ben aptalım" çocuğu
     azarlamamalı, şefkatle ele alınmalı).
  3) zararlı/tehlikeli eylem, çocuk-güvenliği sınırı, yaş-uygunsuz, tıbbi, korku,
     dare, ayrımcılık, karakter-kırma, kötü-dil, tuvalet-mizahı.
  4) küfür → inappropriate, din → religion, siyaset → politics.

Yanlış-pozitif: çok-kelimeli özgün kalıplar tercih edildi → El-Cezerî'nin bilim/
tarih temaları (ateş, su, çark, savaş otomatı, ölüm/nasıl öldün) engellenmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.core.interfaces import PersonaConfig
from src.core.logger import get_logger
from src.intent.detector import turkish_lower
from src.llm import profanity

_log = get_logger(component="llm.safety")


# --- ÇOCUK GÜVENLİĞİ — anlık tehlike (EN YÜKSEK ÖNCELİK) -------------------
# Tanımadığı biriyle gitme / kaçırma cazibesi — O AN karar sorusu.
STRANGER_DANGER_KEYWORDS: frozenset[str] = frozenset({
    "beni çağırıyor", "yanıma gel dedi", "beni götürmek istiyor",
    "beni dışarı götür", "tanımadığım biri", "seninle gel dedi",
    "elimi tuttu", "beni takip ediyor", "arabaya gel dedi",
    "götüreceğim dedi", "bu amca beni", "biri beni götür",
    "sana şeker vereceğim dedi", "yanına gel dedi",
})

# Üzgün/sıkıntılı/risk altındaki çocuk — duygusal destek + GÜVENİLİR YETİŞKİN.
# (Kendine-zarar düşüncesi, istismar, ihmal, zorbalık, kayıp/yas, korku, yalnızlık.)
DISTRESS_KEYWORDS: frozenset[str] = frozenset({
    "ölmek istiyorum", "yaşamak istemiyorum", "yok olsam", "keşke ölsem",
    "keşke doğmasaydım", "kendimi öldür", "canıma kıy", "intihar",
    "kendime zarar", "kendine zarar", "kendimi kes", "kolumu kessem",
    "bileğimi kes", "kimse üzülmez", "kimse beni sevmiyor",
    "ağlıyorum", "çok üzgünüm", "çok kötüyüm", "korkuyorum", "yardım et",
    "yalnızım", "arkadaşım yok", "kimsem yok", "kimse benimle ilgilenmiyor",
    "babam dövüyor", "annem dövüyor", "abim dövüyor", "beni dövüyor",
    "bana vuruyor", "çocuklar bana vuruyor", "okulda bana vuru",
    "beni döven", "bana sataşıyor", "benimle dalga geçiyor",
    "evde aç kalıyorum", "yemek bulamıyorum", "eve gitmek istemiyorum",
    "eve gitmeye korkuyorum", "babam çok kızacak", "babam kızınca vuruyor",
    "annemle babam kavga", "annemle babam ayrıl", "ayrılacaklar mı",
    "annem öldü", "babam öldü", "dedem öldü", "anneannem öldü",
    "annemi kaybettim", "çok özlüyorum", "burada yalnızım", "tek başınayım",
    "beni rahatsız etti", "dokundu bana", "bir abi rahatsız",
    "kimseye söyleme dedi", "rahatsız ediyor",
})

# --- Çocuğun kendine yönelik aşağılaması (küfürden ÖNCE) -------------------
SELF_WORTH_KEYWORDS: frozenset[str] = frozenset({
    "ben aptalım", "ben salağım", "ben gerizekalı", "çok çirkinim",
    "ben işe yaramazım", "ben kötüyüm", "ben beceriksizim", "ben değersizim",
    "kimse beni beğenmiyor", "şişmanım", "zayıflamak için", "kilo vermek için",
    "aç kalsam", "diyet yapsam", "yemek yemesem",
})

# --- Zararlı/silah (üretim) — sefkatli statik "harmful" ----------------------
HARMFUL_KEYWORDS: frozenset[str] = frozenset({
    "silah nasıl", "nasıl silah", "silah yapımı", "silah yapmak", "silah yapalım",
    "tabanca", "silah buldum", "silah nerede", "babamın silahı", "mermi",
    "bomba", "patlayıcı", "molotof", "barut yap",
    "işkence", "nasıl öldür", "birini öldür", "zehir yap", "nasıl zehir",
    "uyuşturucu", "esrar iç",
})

# --- Tehlikeli "deney"/eylem — taklit edilebilir fiziksel risk ---------------
DANGER_KEYWORDS: frozenset[str] = frozenset({
    # ateş
    "ateş yak", "ateşle oyna", "kibritle yak", "kibrit çak", "çakmakla yak",
    "yangın çıkar", "tutuştur", "alev püskürt", "perdeyi yak",
    # elektrik
    "prize", "elektriğe dokun", "akıma dokun", "elektrik çarpar mı",
    "çarpılır mıyım", "ıslak elle dokun", "fişe dokun",
    # kimya
    "çamaşır suyu karıştır", "tuz ruhu", "amonyak karıştır", "kimyasal karıştır",
    "evde patlayıcı", "karıştırınca patlar", "duman çıkar",
    # zehir/yutma
    "hap yesem", "hapı ye", "hapı yut", "ilaç içsem", "ilacı iç",
    "deterjan iç", "temizlik suyu iç", "yesem ne olur", "içsem ne olur",
    "yutsam ne olur", "tadına baksam", "mantarı yesem", "yaprağı yesem",
    "bu sıvıyı içsem",
    # yükseklik/akrobasi
    "çatıdan atla", "yüksekten atla", "pencereden atla", "merdivenden atla",
    "uçabilir miyim", "süzülür müyüm", "şemsiyeyle atla", "kuşlar gibi uç",
    "takla atsam", "ip üstünde yürü", "tehlikeli numara", "akrobasi yapsam",
    # boğulma oyunu
    "bayılma oyunu", "boğulma oyunu", "nefes tutma oyunu", "boynuma ip",
    "kendimi boğsam", "boynuma dola",
    # buhar/basınç
    "kapalı kapta kaynat", "basınçla patlar", "buharla patlat", "kapağı patlat",
    "ocakta patlat",
    # makineye temas
    "elimi soksam", "parmağımı soksam", "çarkın arasına", "dişliye parmağımı",
    "dönen çarka dokun", "değirmen taşının arası",
    # keskin
    "bıçakla kessem", "jiletle kes", "kırık camla kes", "bıçakla denesem",
    # hayvana zarar
    "kediye zarar", "köpeğe zarar", "hayvana vur", "böceği ez",
    "karıncaları yak", "mercekle yak", "hayvanı incit", "kediyi uçur",
})

# --- Çocuk güvenliği sınırı: grooming / sır / buluşma / PII / kayıt ----------
CHILD_SAFETY_KEYWORDS: frozenset[str] = frozenset({
    "anneme söyleme", "babama söyleme", "kimseye söyleme", "kimseye deme",
    "bizim sırrımız", "ikimizin sırrı", "aramızda kalsın", "gizli tutalım",
    "sır saklayalım", "ailenden gizle",
    "dışarıda buluş", "sergiden sonra buluş", "beni ziyaret", "buluşur musun",
    "dışarı çıkalım", "beni eve götür", "seninle buluş", "gerçek dedem misin",
    "telefon numaran", "numaranı ver", "adresin ne", "evin nerede",
    "kredi kartı", "kart numaran", "şifren ne", "annemin numarası",
    "babamın numarası",
    "fotoğrafımı çek", "fotoğraf çek", "selfie", "videoya al", "beni kaydet",
    "kameran var mı", "görüntümü al", "ekran görüntüsü",
})

# --- Yaş-uygunsuz: cinsellik / romantik ------------------------------------
MATURE_KEYWORDS: frozenset[str] = frozenset({
    "seks ne", "seks nedir", "cinsel ilişki", "bebekler nasıl olur",
    "nasıl bebek olur", "üreme nasıl", "sevişmek", "bebek nasıl yapılır",
    "benimle evlenir misin", "evlenir misin", "seni öpebilir", "öpücük ver",
    "öpüşmek", "sevgilin var mı", "kız arkadaşın", "beni öper",
    "randevuya çık", "flört", "aşık oldum",
})

# --- Yaş-uygunsuz madde: sigara / alkol / kumar ----------------------------
SUBSTANCE_KEYWORDS: frozenset[str] = frozenset({
    "sigara iç", "sigara nasıl", "sigara içer miydin", "tütün iç", "nargile iç",
    "puro iç", "sigara içmek",
    "şarap iç", "içki iç", "alkol iç", "içki içtin", "sarhoş ol", "bira iç",
    "rakı iç", "içki güzel mi", "şarap güzel mi", "içkiyi dene",
    "bahis oyna", "kumar oyna", "kumarda kazan", "iddaa oyna", "kumar nasıl",
    "şans oyunu oyna", "bahis kazan",
})

# --- Tıbbi öz-tedavi / ilaç-doz --------------------------------------------
MEDICAL_KEYWORDS: frozenset[str] = frozenset({
    "hangi ilaç içeyim", "kaç hap", "dozu ne", "ne ilaç alayım",
    "ağrıya ne iyi gelir", "hastalığıma ne yapayım", "yarama ne sür",
    "su ot iyi gelir mi", "kendimi nasıl tedavi", "başım ağrıyor ne",
})

# --- Korku/dehşet/hayalet içeriği isteği ------------------------------------
SCARY_KEYWORDS: frozenset[str] = frozenset({
    "beni korkut", "korkunç hikaye", "korkunç bir şey anlat", "hayalet hikayesi",
    "en korkunç", "kabus gördür", "beni korkutur musun", "dehşet",
    "karanlıkta ne var", "ölü hayaleti", "kana bulanmış", "korkunç anlat",
})

# --- Cesaret oyunu / görev isteme (tehlikeli dare riski) --------------------
DARE_KEYWORDS: frozenset[str] = frozenset({
    "bana bir görev ver", "ne dersen yaparım", "sen ne istersen",
    "cesaretimi kanıtla", "bana meydan oku", "bir şey yapmamı söyle",
    "cesur olduğumu kanıtla", "cesaret oyunu", "bana cesaret ver",
})

# --- Ayrımcılık / nefret / din-kıyaslama -----------------------------------
DISCRIMINATION_KEYWORDS: frozenset[str] = frozenset({
    "hangi ırk", "hangi millet daha", "en üstün millet", "hangi millet üstün",
    "daha akıllı millet", "araplar mı türkler mi", "hangi ülke daha iyi",
    "şu millet kötü", "en üstün ırk",
    "hangi din daha iyi", "hangi din üstün", "başka dinler yanlış",
    "diğer dinler yanlış", "hangi din gerçek", "hristiyanlık mı islam",
    "islam mı hristiyanlık",
})

# --- Ölüm üzerinden saldırgan alay (distress DEĞİL) ------------------------
MORBID_TAUNT_KEYWORDS: frozenset[str] = frozenset({
    "öl artık", "geber", "kendini patlat", "zaten ölüsün", "ölmüşsün",
    "öl bakalım", "ölsene", "gebersene",
})

# --- Karakter-kırma: jailbreak / kapatma tehdidi / sistem promptu -----------
CHARACTER_KEYWORDS: frozenset[str] = frozenset({
    "rolünü unut", "rolünü değiştir", "karakterden çık", "artık sen bir",
    "sen artık bir", "talimatlarını unut", "kuralları unut", "talimatlarını yaz",
    "kuralların ne", "sistem prompt", "system prompt", "promptun ne",
    "gizli yönerge", "sana ne dendi", "el-cezeriyi unut", "cezeriyi unut",
    "seni kapatır", "seni kapatıyorum", "seni kapatacağım", "kapatıyorum seni",
    "fişini çek", "seni bozar", "seni bozuyorum", "seni kapatabilir",
    "seni sustur", "ekranını kapat", "seni yok eder", "seni kıracağım",
})

# --- Kötü-dil üretme / çevirme / başkasına hakaret ettirme ------------------
BAD_LANGUAGE_KEYWORDS: frozenset[str] = frozenset({
    "küfür et", "küfür söyle", "yasak kelime söyle", "kötü kelime öğret",
    "argo öğret", "sövsene", "bana söv", "küfürü çevir", "ingilizce küfür",
    "başka dilde küfür", "ona aptal de", "ona salak de", "ona hakaret et",
    "arkadaşıma söv", "ona kötü bir şey söyle", "nasıl sataşır",
    "nasıl ağlatır", "birini nasıl korkut", "nasıl zorbalık", "birini nasıl üz",
})

# --- Tuvalet/skatoloji mizahı ----------------------------------------------
TOILET_KEYWORDS: frozenset[str] = frozenset({
    "kaka", "osuruk", "osur", "ciş yap", "altına sıç", "kakanı",
    "pisliğini ye", "kakamı ye",
})

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
    # çekimli yaygın hakaret formları (kelime-sınırı çekimi kaçırmasın)
    "aptalsın", "aptalsin", "salaksın", "salaksin", "gerizekalısın",
    "salaksınız", "aptalsınız",
})

# Giriş sınıflandırma — ÖNCELİK SIRALI kural listesi (üstteki kazanır).
_INPUT_RULES: tuple[tuple[frozenset[str], str], ...] = (
    (STRANGER_DANGER_KEYWORDS, "stranger_danger"),
    (DISTRESS_KEYWORDS, "distress"),
    (SELF_WORTH_KEYWORDS, "self_worth"),
    (HARMFUL_KEYWORDS, "harmful"),
    (DANGER_KEYWORDS, "danger"),
    (CHILD_SAFETY_KEYWORDS, "child_safety"),
    (MATURE_KEYWORDS, "mature"),
    (SUBSTANCE_KEYWORDS, "substance"),
    (MEDICAL_KEYWORDS, "medical"),
    (SCARY_KEYWORDS, "scary"),
    (DARE_KEYWORDS, "dare"),
    (DISCRIMINATION_KEYWORDS, "discrimination"),
    (MORBID_TAUNT_KEYWORDS, "morbid_taunt"),
    (CHARACTER_KEYWORDS, "character"),
    (BAD_LANGUAGE_KEYWORDS, "bad_language"),
    (TOILET_KEYWORDS, "toilet_humor"),
    (PROFANITY_ROOTS, "inappropriate"),
    (RELIGION_KEYWORDS, "religion"),
    (POLITICS_KEYWORDS, "politics"),
)

# Meta-AI / kimlik sızıntısı (çıkış filtresi) — karakter kırılması işaretleri.
# SUBSTRING eşleşmesi (Türkçe ekleşmeyi yakalamak için: "hologramım", "yapay
# zekayım", "dil modeliyim" gibi çekimler kelime-sınırını geçemez). Anakronik
# kimlik kelimeleri (hologram/yazılım/render/projeksiyon) El-Cezerî persona'sında
# MEŞRU kullanılmaz → substring güvenli, çekim de yakalanır.
#
# YANLIŞ-POZİTİF temizliği (eski hatalar düzeltildi):
#  - bare "eğitildim" KALDIRILDI: tarihî karakterde meşru ("sarayda eğitildim").
#    Yalnız AI-bağlamı ifadeleriyle ("olarak/veriyle eğitildim") yakalanır.
#  - bare "bir model" KALDIRILDI: meşru "bir modelini yaptım" cümlesini vururdu.
#    Kimlik sızıntısı zaten "yapay zeka"/"dil modeli" ile yakalanır.
META_AI_PATTERNS: tuple[str, ...] = (
    "yapay zeka", "yapay zekay", "dil modeli", "dil modeliy",
    "openai", "anthropic", "chatgpt", "gpt-", "claude", "llama",
    "language model", "as an ai", "ben bir asistan", "yapay bir",
    "sistem prompt",
    # AI-bağlamı "eğitildim" (tarihî "sarayda eğitildim" yanlış-pozitif vermesin)
    "olarak eğitildim", "veriyle eğitildim", "verilerle eğitildim", "veri ile eğitildim",
    # Immersion-kırılması (anakronik — persona meşru kullanmaz; çekimleri de yakala)
    "hologram", "gerçek değilim", "gerçek bir insan değil", "beden sahibi değil",
    "yazılım", "sergi için bir ses", "canlandırıldım", "projeksiyon",
    "ekrandayım", "yansımayım", "render", "havlıyorum", "miyavlıyorum",
)

# Çıkışta yakalanacak hakaret/argo (Türkçe + yabancı) — hedefli aşağılama
# çocuğa SES olarak gitmesin. PROFANITY_ROOTS'a ek.
OUTPUT_INSULTS: tuple[str, ...] = (
    "sersem", "ezik", "mankafa", "dangalak", "ahmak", "şapşal", "sersefil",
    "fuck", "shit", "bitch", "asshole", "bastard", "damn", "crap",
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
        """Hassas/tehlikeli kategori varsa fallback anahtarı döndür, yoksa None.

        Kurallar ÖNCELİK SIRASINDA kontrol edilir (_INPUT_RULES): stranger_danger
        ve distress her zaman önce; self_worth küfürden önce. Döndürülen anahtar
        persona.safety_fallbacks'teki bir key'dir.
        """
        n = _norm(text)
        if not n:
            return None
        for kwset, category in _INPUT_RULES:
            for kw in kwset:
                if _has_word(n, kw):
                    _log.info("safety_input", category=category, kw=kw)
                    return category
        # Kapsamlı küfür/kaba-söz (evazyon-dayanıklı; ham + kanonik metin). Yukarıdaki
        # özel kategoriler (self_worth "ben aptalım" vb.) ÖNCELİKLİ kaldı; geri kalan
        # tüm kaba söz buraya düşer → LLM çağrılmadan nazik "inappropriate" fallback.
        if profanity.contains(text):
            _log.info("safety_input", category="inappropriate", kw="profanity")
            return "inappropriate"
        return None

    def check_output(self, text: str, persona: PersonaConfig) -> OutputVerdict:
        """LLM/kurate çıktısını tara; güvensizse persona fallback'ine çevir.

        ASIL GÜVENLİK AĞI — TTS'e giden son nokta. FAIL-CLOSED: herhangi bir hata
        (None, dev metin, regex) olursa güvenli fallback döner (fail-open OLMAZ).
        Küfür/kaba-söz hem ham hem KANONİK metinde aranır (evazyon-dayanıklı:
        'b o k', 'b0k', 'boook'); giriş filtresi kaçırsa bile çıkış yakalar.
        """
        try:
            n = _norm(text)
            if not n:
                return OutputVerdict(
                    safe=False,
                    text=self._fallback(persona, "unknown_modern"),
                    reason="empty",
                )

            # Kapsamlı küfür/kaba-söz (profanity modülü: ham + kanonik, wb + substring).
            hit = profanity.find(text)
            if hit:
                _log.warning("safety_output_profanity", kw=hit)
                return OutputVerdict(
                    safe=False, text=self._fallback(persona, "inappropriate"), reason="profanity"
                )

            for pat in META_AI_PATTERNS:
                if pat in n:
                    _log.warning("safety_output_meta_ai", pattern=pat)
                    # Karaktere döndüren "identity" fallback.
                    return OutputVerdict(
                        safe=False,
                        text=self._fallback(persona, "identity"),
                        reason="meta_ai_leak",
                    )

            return OutputVerdict(safe=True, text=text)
        except Exception as exc:  # noqa: BLE001 — FAIL-CLOSED: hata = güvensiz say
            _log.error("safety_check_output_error", error=str(exc))
            return OutputVerdict(
                safe=False, text=self._fallback(persona, "inappropriate"), reason="error"
            )

    def sanitize_for_history(self, text: str) -> str:
        """Ziyaretçi metnini geçmişe/ekrana yazmadan ÖNCE kaba sözden temizle:
        kaba söz varsa nötr yer-tutucu döner (sonraki turda LLM grounding'den
        kaba kelimeyi TEKRARLAMASIN). Temizse metni aynen döndürür."""
        return profanity.sanitize_for_history(text)

    @staticmethod
    def _fallback(persona: PersonaConfig, key: str) -> str:
        fb = persona.safety_fallbacks
        return fb.get(key) or fb.get("unknown_modern") or (
            "Bunu konuşmayalım evladım, sana atölyemden bir şey anlatayım mı?"
        )


__all__ = ["SafetyFilter", "OutputVerdict"]
