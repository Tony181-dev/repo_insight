PYTHON ?= python3
WS     ?= .
VERSION = $(shell $(PYTHON) -c "import repomap; print(repomap.__version__)")

.PHONY: run test pyz release wheel install clean

run:                ## chay tu source:  make run WS=~/your_workspace_root
	$(PYTHON) -m repomap $(WS)

test:               ## chay unit test (chi can python stdlib)
	$(PYTHON) -m unittest discover -s tests -v

pyz:                ## dong goi 1 file chay duoc: dist/repomap.pyz  ->  python3 repomap.pyz ~/your_workspace_root
	$(PYTHON) tools/build_pyz.py --test

release:            ## goi phat hanh cho nguoi dung (khong kem repo): dist/repomap-<ver>.tar.gz va .zip
	$(PYTHON) tools/make_release.py --test

wheel:              ## dong goi wheel: dist/repomap-*.whl  (pip install dist/repomap-*.whl)
	$(PYTHON) -m pip wheel . -w dist --no-deps --no-build-isolation

install:            ## cai vao ~/.local (lenh `repomap`)
	$(PYTHON) -m pip install --user .

clean:
	rm -rf build dist *.egg-info
	find . -name __pycache__ -prune -exec rm -rf {} +
