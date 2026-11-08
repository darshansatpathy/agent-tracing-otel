.PHONY: install check dashboards demo report

install:
	pip install -e ".[dev]"

check:
	ruff check .
	ruff format --check .
	mypy
	pytest --cov --cov-report=term-missing

dashboards:
	python deploy/grafana/build.py

demo:
	docker compose up -d
	agenttrace simulate --tasks 2000 --otlp http://localhost:4318
	@echo "Grafana: http://localhost:3000/d/agent-cost"

report:
	agenttrace simulate --tasks 5000 --jsonl spans.jsonl && agenttrace report spans.jsonl
