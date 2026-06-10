#!/bin/bash
# BilimFest — sergi öncesi/sonrası tam yedek snapshot.
#
# Yedeklenenler:
#   data/models/                  — Llama, Whisper, XTTS, e5 (büyük)
#   data/responses/vector_store/  — ChromaDB persistent
#   data/voices/                  — Ses örnekleri (sergi gizli IP)
#   config.yaml, config.production.yaml
#   src/llm/responses/            — RAG seed JSON'ları
#   src/llm/persona/              — persona configs
#
# Kullanım:
#   bash scripts/backup_state.sh                          # /tmp/bilimfest-<tarih>.tar.gz
#   bash scripts/backup_state.sh /backup/                 # /backup/bilimfest-<tarih>.tar.gz

set -euo pipefail

OUT_DIR="${1:-/tmp}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="${OUT_DIR}/bilimfest-${TIMESTAMP}.tar.gz"

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "BilimFest yedek snapshot oluşturuluyor: $OUT_FILE"
echo "Proje kök: $PROJECT_ROOT"

mkdir -p "$OUT_DIR"

# Kontrol: önemli dosyalar var mı
for p in data/models data/responses/vector_store config.yaml src/llm/responses src/llm/persona; do
    if [ ! -e "$p" ]; then
        echo "UYARI: $p yok, atlanıyor"
    fi
done

# tar oluştur (sıkıştırma seviyesi 6 - boyut/hız dengesi)
tar -czf "$OUT_FILE" \
    --exclude="data/tts_cache" \
    --exclude="*.log" \
    --exclude="__pycache__" \
    data/models \
    data/responses \
    data/voices \
    config.yaml \
    config.production.yaml \
    src/llm/responses \
    src/llm/persona \
    2>&1 | tail -5

SIZE=$(du -h "$OUT_FILE" | cut -f1)
echo "[OK] Yedek alındı: $OUT_FILE ($SIZE)"
echo ""
echo "Geri yükleme:"
echo "  tar -xzf $OUT_FILE -C $PROJECT_ROOT"
