# BilimFest

Pepper's Ghost hologram interaktif bilim insanı sergisi. Ziyaretçi mikrofona konuşur,
AI cevap üretir, 3D karakter (Cezerî, Ali Kuşçu, Cahit Arf, Marie Curie, Einstein)
sahnede belirir ve cevap verir.

## Pipeline

```
Mic → STT (Whisper) → Intent → LLM (Llama + RAG)
    → TTS (XTTS) → Lip-Sync (Audio2Face) → Unreal MetaHuman → Projeksiyon
```

Tüm AI bileşenleri **abstraction layer** arkasında: lokal ↔ cloud geçişi tek satır config değişikliği.

## Geliştirme Aşamaları

8 faz halinde geliştirilir — `prompts, readme and pipeline/` klasöründeki master prompt'lara bak.

- Phase 1 — İskelet + abstraction layer + mock provider'lar (tamamlandı)
- **Phase 2 (şu an)** — Whisper Large v3 lokal STT + VAD + canlı mikrofon
- Phase 3 — Llama LLM + RAG hazır cevap havuzu
- Phase 4 — XTTS ses klonu
- Phase 5 — Audio2Face lip-sync
- Phase 6 — Orchestrator state machine + sahne yönetimi
- Phase 7 — Unreal Engine köprüsü (Live Link)
- Phase 8 — E2E test + deployment + dress rehearsal

## Hızlı Başlangıç

Gereksinim: Python 3.11+, [uv](https://github.com/astral-sh/uv) önerilir (yoksa pip).

```bash
make install     # temel bağımlılıklar (Phase 1: mock pipeline)
make test        # tüm testleri çalıştır
make run         # geliştirme sunucusu (http://localhost:8000)
```

### Phase 2 — Whisper STT (gerçek mikrofon)

```bash
# 1) PyTorch CUDA wheel + STT bağımlılıkları (~2 GB)
make install-stt

# 2) Whisper Large v3 modelini indir (~3 GB) → data/models/whisper
make download-models

# 3) PC mikrofon ile canlı test (8 sn konuş)
make test-microphone

# 4) Latency benchmark (fixture .wav dosyaları gerekir)
make benchmark-stt
```

Config.yaml `stt.provider = "whisper_local"` ile gelir. Mock'a dönmek için
`BFEST__STT__PROVIDER=mock` env değişkeni veya config.yaml düzenle.

Smoke test:

```bash
curl http://localhost:8000/health
# {"status": "ok", ...}

python scripts/test_e2e.py
# Tüm mock pipeline IDLE → WELCOME → ... → IDLE
```

## Klasör Yapısı

```
bilimfest/
├── src/
│   ├── core/              # interfaces, config, factory, logger, metrics, errors
│   ├── stt/               # STT provider'lar (whisper_local, mock, ...)
│   ├── llm/               # LLM provider'lar + RAG + persona configs
│   ├── tts/               # TTS provider'lar
│   ├── lipsync/           # Audio2Face vb.
│   ├── intent/            # niyet tespiti
│   ├── orchestrator/      # FastAPI app + state machine + session
│   └── unreal_bridge/     # Live Link + scene control
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── scripts/               # yardımcı script'ler (test_e2e, model indir, vb.)
├── data/                  # modeller, ses örnekleri, RAG store (gitignore)
├── deployment/            # systemd, monitoring
├── config.yaml            # ana konfigürasyon
└── pyproject.toml
```

## Konfigürasyon

`config.yaml` tüm sistem ayarlarını tutar. Provider seçimi buradan yapılır:

```yaml
stt:
  provider: "mock"        # mock | whisper_local | deepgram_cloud
llm:
  provider: "mock"        # mock | llama_local | claude_cloud
tts:
  provider: "mock"        # mock | xtts_local | elevenlabs_cloud
lipsync:
  provider: "mock"        # mock | audio2face
```

Phase 1'de hepsi `mock` — gerçek AI bileşenleri Phase 2+ ile gelir.

Env override: `BFEST__STT__PROVIDER=whisper_local` (nested için `__` delimiter).

## Mimari ve Protokoller

- `prompts, readme and pipeline/architecture.md` — sistem mimarisi, performans hedefleri
- `prompts, readme and pipeline/api_contracts.md` — modüller arası protokoller, Unreal bridge
- `prompts, readme and pipeline/data_schemas.md` — persona, session, metrics, error şemaları

## Geliştirme Komutları

```bash
make lint       # ruff + mypy
make format     # ruff format + fix
make test-unit  # sadece birim testler
make clean      # cache temizliği
```
