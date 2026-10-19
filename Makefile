PYTHON ?= python3
WS     ?= .
VERSION = $(shell $(PYTHON) -c "import repomap; print(repomap.__version__)")

.PHONY: run test pyz wheel install clean

run:                ## chay tu source:  make run WS=/path/to/workspace
	$(PYTHON) -m repomap $(WS)

test:               ## chay unit test (chi can python stdlib)
	$(PYTHON) -m unittest discover -s tests -v

pyz:                ## dong goi 1 file chay duoc: dist/repomap.pyz  ->  ./repomap.pyz /path/ws
	rm -rf build/pyz && mkdir -p build/pyz dist
	cp -r repomap build/pyz/
	find build/pyz -name __pycache__ -prune -exec rm -rf {} +
	$(PYTHON) -m zipapp build/pyz -m "repomap.cli:main" -p "/usr/bin/env python3" -o dist/repomap.pyz -c
	@echo "-> dist/repomap.pyz ($(VERSION))"

wheel:              ## dong goi wheel: dist/repomap-*.whl  (pip install dist/repomap-*.whl)
	$(PYTHON) -m pip wheel . -w dist --no-deps --no-build-isolation

install:            ## cai vao ~/.local (lenh `repomap`)
	$(PYTHON) -m pip install --user .

clean:
	rm -rf build dist *.egg-info
	find . -name __pycache__ -prune -exec rm -rf {} +
