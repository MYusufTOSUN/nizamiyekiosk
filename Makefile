.PHONY: install install-stt install-cuda dev test test-e2e test-integration lint format clean run run-prod tree download-models test-microphone

install:
	uv pip install -e ".[dev]" || pip install -e ".[dev]"

install-stt: install-cuda
	uv pip install -e ".[stt]" || pip install -e ".[stt]"

install-cuda:
	# PyTorch CUDA 12.1 wheel (cu121 → 4060/4070 GPU'larıyla uyumlu)
	pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

install-llm: install-llm-cuda
	uv pip install -e ".[llm]" || pip install -e ".[llm]"

install-llm-cuda:
	# llama-cpp-python CUDA-linked binary wheel (--index-url, --no-deps to avoid backtrack):
	pip install llama-cpp-python==0.2.88 --index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 --no-cache-dir --no-deps

install-tts:
	uv pip install -e ".[tts]" || pip install -e ".[tts]"

test-tts:
	python scripts/test_tts.py "Aleyküm selam evladım, ben El-Cezerî" --play

dev: install

test:
	pytest tests/ -v --cov=src --cov-report=term-missing

test-unit:
	pytest tests/unit -v

test-integration:
	pytest tests/integration -v

test-e2e:
	pytest tests/integration -v -m "e2e"

lint:
	ruff check src tests
	mypy src

format:
	ruff format src tests
	ruff check --fix src tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf build dist *.egg-info

run:
	uvicorn src.orchestrator.app:app --reload --host 0.0.0.0 --port 8000

run-prod:
	uvicorn src.orchestrator.app:app --host 0.0.0.0 --port 8000 --workers 1

tree:
	tree src/ -I '__pycache__|*.pyc'

download-models:
	python scripts/download_models.py

test-microphone:
	python scripts/test_microphone.py

benchmark-stt:
	python scripts/benchmark_stt.py

build-rag:
	python scripts/build_rag_store.py --reset

test-llm:
	python scripts/test_llm.py
