PY ?= .venv/bin/python

setup:            ## create .venv and install everything
	python3 -m venv .venv
	$(PY) -m pip install -q -e '.[dev,langgraph,viz]'

platform:         ## start the food-delivery platform API on :8700
	$(PY) -m uvicorn services.app:app --port 8700

test:             ## run the test suite (no API key needed)
	$(PY) -m pytest -q

.PHONY: setup platform test
