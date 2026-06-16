"""M2: yeni karakter iskeleti üret.

Kullanıcı karakteri belirleyince tek komutla persona dosyası + boş RAG cevap
JSON'u + ses klasörü oluşturur. Sonra:
  1. src/llm/persona/<id>.py içindeki system_prompt + fallback'leri doldur
  2. src/llm/persona/__init__.py PERSONAS dict'ine ekle (script hatırlatır)
  3. src/llm/responses/<id>.json'a ~200 cevap yaz (cezeri.json'u örnek al)
  4. data/voices/<id>/ref_01..03.wav ses kayıtlarını koy
  5. python scripts/build_rag_store.py <id>
  6. src/intent/keywords.py CHARACTER_KEYWORDS'e tetikleyici kelimeler ekle

Kullanım:
    python scripts/new_character.py ali_kuscu --name "Ali Kuşçu" --era "1403-1474"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PERSONA_TEMPLATE = '''"""{name} persona konfigürasyonu — TASLAK, doldurulmalı."""

from __future__ import annotations

from src.core.interfaces import PersonaConfig

{const_name} = PersonaConfig(
    id="{cid}",
    name="{name}",
    full_name="{name}",
    era="{era}",
    birthplace="",
    expertise=[],
    famous_works=[],
    voice_id="{cid}",
    rag_collection="{cid}_responses",
    system_prompt=(
        "Sen {name}'sin. [BURAYI DOLDUR: karakter, konuşma tarzı, ne bilir/bilmez, "
        "modern konuları reddetme kuralı + few-shot örnekler — cezeri.py'yi örnek al]\\n"
    ),
    safety_fallbacks={{
        "religion": "Bu derin bir konu, bunu âlimlere bırakalım. Başka ne sormak istersin?",
        "politics": "Siyasete aklım ermez. Hadi ilimden konuşalım.",
        "inappropriate": "Böyle konuşmayalım, saygıyla devam edelim. Ne öğrenmek istersin?",
        "unknown_modern": "Bu benim dönemimden sonraki bir şey. Ama benzeri için sana anlatabilirim.",
        "personal_death": "Bunlar geride kaldı. Bugün seninle olabildim, bu yeter.",
    }},
    initial_greeting="Merhaba, ben {name}. Ne öğrenmek istersin?",
    farewell_messages=[
        "Vakit doldu, yine bekleriz.",
        "Görüşmek üzere, meraklı kal.",
        "Hadi sağlıcakla.",
    ],
)
'''

RESPONSES_TEMPLATE = {
    "persona_id": "",
    "version": "1.0",
    "language": "tr",
    "notes": "TASLAK — cezeri.json'u örnek alarak ~200 cevaba çıkar.",
    "responses": [
        {
            "id": "{cid}_001",
            "intent_keywords": ["selam", "merhaba"],
            "question_examples": ["Merhaba", "Selam"],
            "response": "Merhaba evladım, hoş geldin. Ne konuşalım bugün?",
            "topic": "selam",
            "difficulty": "kolay",
        }
    ],
}

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="BilimFest yeni karakter iskeleti")
    ap.add_argument("cid", help="Karakter id (kebab/snake, örn. ali_kuscu)")
    ap.add_argument("--name", required=True, help='Görünen ad (örn. "Ali Kuşçu")')
    ap.add_argument("--era", default="", help='Dönem (örn. "1403-1474")')
    args = ap.parse_args()

    cid = args.cid.strip().lower()
    const_name = cid.upper() + "_PERSONA"

    persona_path = ROOT / "src" / "llm" / "persona" / f"{cid}.py"
    responses_path = ROOT / "src" / "llm" / "responses" / f"{cid}.json"
    voices_dir = ROOT / "data" / "voices" / cid

    if persona_path.exists() or responses_path.exists():
        print(f"HATA: {cid} için dosyalar zaten var. Üzerine yazmıyorum.")
        return 1

    persona_path.write_text(
        PERSONA_TEMPLATE.format(name=args.name, era=args.era, cid=cid, const_name=const_name),
        encoding="utf-8",
    )

    data = json.loads(json.dumps(RESPONSES_TEMPLATE))  # deep copy
    data["persona_id"] = cid
    data["responses"][0]["id"] = f"{cid}_001"
    responses_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    voices_dir.mkdir(parents=True, exist_ok=True)
    (voices_dir / ".gitkeep").touch()

    print("[OK] Oluşturuldu:")
    print(f"  - {persona_path.relative_to(ROOT)}")
    print(f"  - {responses_path.relative_to(ROOT)}")
    print(f"  - {voices_dir.relative_to(ROOT)}/ (ref_01..03.wav koy)")
    print("\nSON ADIMLAR (elle):")
    print(f"  1. {persona_path.name} system_prompt + alanları doldur (cezeri.py örnek)")
    print(f"  2. src/llm/persona/__init__.py PERSONAS'a {const_name} ekle")
    print(f"  3. {cid}.json'u ~200 cevaba çıkar")
    print(f"  4. data/voices/{cid}/ref_01..03.wav ses kayıtları")
    print(f"  5. src/intent/keywords.py CHARACTER_KEYWORDS'e '{cid}' kelimeleri")
    print(f"  6. python scripts/build_rag_store.py {cid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
