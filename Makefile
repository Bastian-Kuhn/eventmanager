PYTHON ?= .venv/bin/python
VERSION := $(shell grep -E '^version\s*=' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')

.PHONY: help version clean build check upload tag release

help:
	@echo "Available targets:"
	@echo "  make version      - Print the current version ($(VERSION))"
	@echo "  make clean        - Remove build artefacts (build/, dist/, *.egg-info)"
	@echo "  make build        - Build sdist and wheel into dist/"
	@echo "  make check        - twine check dist/*"
	@echo "  make upload       - twine upload dist/* (uses ./.pypirc)"
	@echo "  make tag          - Create and push git tag v$(VERSION)"
	@echo "  make release      - clean + build + check + upload + tag"
	@echo ""
	@echo "Requires: $(PYTHON) -m pip install build twine"

version:
	@echo $(VERSION)

clean:
	rm -rf build/ dist/ *.egg-info

build: clean
	$(PYTHON) -m build

check:
	$(PYTHON) -m twine check dist/*

upload:
	$(PYTHON) -m twine upload --config-file ./.pypirc dist/*

tag:
	git tag -a v$(VERSION) -m "Release $(VERSION)"
	git push origin v$(VERSION)

release: build check upload tag
	@echo "Released $(VERSION) to PyPI"
