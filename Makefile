# Makefile for gpu-cluster-monitor

# Default Python interpreter
PYTHON ?= python3

# Virtual environment directory
VENV_DIR = .venv

.PHONY: all help venv install clean build publish_test publish lint

all: help

help:
	@echo "Available targets:"
	@echo "  venv          - Create a Python virtual environment in $(VENV_DIR)"
	@echo "  install       - Install the package in editable mode with dev dependencies (activates venv if present)"
	@echo "  build         - Build the package (sdist and wheel)"
	@echo "  clean         - Remove build artifacts and __pycache__ directories"
	@echo "  publish_test  - Upload package to TestPyPI"
	@echo "  publish       - Upload package to PyPI"
	@echo "  lint          - Run linters/formatters (placeholder)"

# Check if virtual environment exists and activate it for subsequent commands if so
# This is a bit of a hack for Makefiles but often useful.
# For commands that need the venv, prepend with $(VENV_PYTHON) or $(VENV_PIP)
ifeq ($(wildcard $(VENV_DIR)/bin/activate),$(VENV_DIR)/bin/activate)
	VENV_PYTHON = $(VENV_DIR)/bin/python
	VENV_PIP = $(VENV_DIR)/bin/pip
	VENV_TWINE = $(VENV_DIR)/bin/twine
	VENV_BUILD = $(VENV_DIR)/bin/python -m build
	ACTIVATE_VENV = . $(VENV_DIR)/bin/activate && \
else
	VENV_PYTHON = $(PYTHON)
	VENV_PIP = $(PYTHON) -m pip
	VENV_TWINE = $(PYTHON) -m twine
	VENV_BUILD = $(PYTHON) -m build
	ACTIVATE_VENV = # No-op
endif

venv: $(VENV_DIR)/bin/activate

$(VENV_DIR)/bin/activate:
	@echo "Creating virtual environment in $(VENV_DIR)..."
	$(PYTHON) -m venv $(VENV_DIR)
	@echo "Virtual environment created. Activate with: source $(VENV_DIR)/bin/activate"
	@echo "Then run 'make install' to install dependencies."

install: # $(VENV_DIR)/bin/activate # Optional dependency on venv creation
	@echo "Installing package in editable mode and dev dependencies..."
	$(ACTIVATE_VENV) $(VENV_PIP) install -e .[dev] # Assuming you might add a [dev] extra in pyproject.toml
	# If no [dev] extra, then: 
	# $(ACTIVATE_VENV) $(VENV_PIP) install -e .
	$(ACTIVATE_VENV) $(VENV_PIP) install --upgrade build twine
	@echo "Installation complete."

build:
	@echo "Building package..."
	$(ACTIVATE_VENV) $(VENV_BUILD)
	@echo "Build complete. Artifacts in dist/"

clean:
	@echo "Cleaning build artifacts..."
	rm -rf build dist *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
	@echo "Clean complete."

publish_test:
	@echo "Publishing to TestPyPI..."
	$(ACTIVATE_VENV) $(VENV_TWINE) upload --repository testpypi dist/*

publish:
	@echo "Publishing to PyPI..."
	$(ACTIVATE_VENV) $(VENV_TWINE) upload dist/*

lint:
	@echo "Linting and formatting (placeholder)..."
	# Add your linting/formatting commands here, e.g.:
	# $(ACTIVATE_VENV) ruff check .
	# $(ACTIVATE_VENV) black .
	@echo "Linting complete (placeholder)."

# Note: For the 'install' target, I've used 'pip install -e .[dev]'.
# This assumes you might add an optional [dev] dependency group in your pyproject.toml like:
# [project.optional-dependencies]
# dev = ["build", "twine", "ruff", "black"] # etc.
# If you don't plan to do this, change the install line to:
# $(ACTIVATE_VENV) $(VENV_PIP) install -e .
# And ensure build/twine are installed separately or via the venv setup.
# The current 'install' target also installs build and twine explicitly.
