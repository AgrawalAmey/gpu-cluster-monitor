# Makefile for gpu-cluster-monitor

# Default Python interpreter
PYTHON ?= python3

# Virtual environment directory
VENV_DIR = .venv

.PHONY: all help venv install clean build publish_test publish lint format

all: help

help:
	@echo "Available targets:"
	@echo "  venv          - Create a Python virtual environment in $(VENV_DIR)"
	@echo "  install       - Install the package in editable mode with dev dependencies (assumes venv is active)"
	@echo "  build         - Build the package (sdist and wheel) (assumes venv is active)"
	@echo "  clean         - Remove build artifacts and __pycache__ directories"
	@echo "  publish_test  - Upload package to TestPyPI (assumes venv is active)"
	@echo "  publish       - Upload package to PyPI (assumes venv is active)"
	@echo "  lint          - Run linters and formatters (assumes venv is active)"
	@echo "  format        - Run formatters (assumes venv is active)"

# Define commands assuming the virtual environment is active.
# The user is responsible for activating the venv (e.g., 'source .venv/bin/activate')
# before running targets that depend on it (install, build, lint, etc.).
VENV_PYTHON = $(PYTHON)
VENV_PIP = $(PYTHON) -m pip
VENV_TWINE = $(PYTHON) -m twine
VENV_BUILD = $(PYTHON) -m build

venv: $(VENV_DIR)/bin/activate

$(VENV_DIR)/bin/activate:
	@echo "Creating virtual environment in $(VENV_DIR)..."
	$(PYTHON) -m venv $(VENV_DIR)
	@echo "Virtual environment created. Activate with: source $(VENV_DIR)/bin/activate"
	@echo "Then run 'make install' to install dependencies."

install: # $(VENV_DIR)/bin/activate # Optional dependency on venv creation
	@echo "Installing package in editable mode and dev dependencies..."
	$(VENV_PIP) install -e .[dev] 
	# $(VENV_PIP) install -e .
	$(VENV_PIP) install --upgrade build twine
	@echo "Installation complete."

build:
	@echo "Building package..."
	$(VENV_BUILD)
	@echo "Build complete. Artifacts in dist/"

clean:
	@echo "Cleaning build artifacts..."
	rm -rf build dist *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
	@echo "Clean complete."

publish_test:
	@echo "Publishing to TestPyPI..."
	$(VENV_TWINE) upload --repository testpypi dist/*

publish:
	@echo "Publishing to PyPI..."
	$(VENV_TWINE) upload dist/*

lint:
	@echo "Linting and formatting..."
	ruff check .
	ruff format --check .
	@echo "Linting complete."

format:
	@echo "Formatting..."
	ruff format .
	@echo "Formatting complete."
