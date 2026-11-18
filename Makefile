.PHONY: install test lint type eval demo crash-demo scenarios
install:
	pip install -e ".[dev]"
test:
	pytest --cov=incidentops --cov-report=term-missing:skip-covered
lint:
	ruff check . && ruff format --check .
type:
	mypy src
eval:
	incidentops eval --out evals/RESULTS.md
demo:
	incidentops run --scenario 7 --live
crash-demo:
	python scripts/crash_demo.py
scenarios:
	python scripts/make_scenarios.py
