.PHONY: install dev test test-e2e test-integration lint format clean run run-prod tree

install:
	uv pip install -e ".[dev]" || pip install -e ".[dev]"

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
