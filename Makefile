# Argon Browser Makefile (LibreWolf fork base — Linux Flatpak & Windows)

ifeq ($(OS),Windows_NT)
RUN_PYTHON ?= node scripts/run-python.mjs
else
RUN_PYTHON ?= python3
endif

version := $(shell cat version)
release := $(shell cat release)
ff_source_dir=firefox-$(version)
ff_source_tarball=firefox-$(version).source.tar.xz
argon_source_dir=argon-$(version)-$(release)
argon_source_tarball=argon-$(version)-$(release).source.tar.gz

default: help

help:
	@printf "Argon Browser (LibreWolf fork) — build targets:\n"
	@printf "  all             : prepare patched Argon source tree (check + dir + patches)\n"
	@printf "  dir             : extract Firefox $(version) into $(argon_source_dir)\n"
	@printf "  patches         : apply LibreWolf & Argon patches, branding, and overlay\n"
	@printf "  build           : compile Argon inside $(argon_source_dir)\n"
	@printf "  package         : package Argon build inside $(argon_source_dir)\n"
	@printf "  flatpak         : build the single-file Flatpak bundle (io.github.argon_browser.Argon)\n"
	@printf "  win-portable    : build the Windows portable ZIP package\n"
	@printf "  win-installer   : build the Windows NSIS installer\n"
	@printf "  verify-windows  : verify built Windows portable ZIP / installer packages\n"
	@printf "  run             : run compiled Argon binary\n"
	@printf "  clean           : remove $(argon_source_dir)\n"
	@printf "  distclean       : remove $(argon_source_dir) and downloaded source archives\n"

all: check dir patches

check:
	@which wget > /dev/null
	@which tar > /dev/null
	@which unzip > /dev/null
ifeq ($(OS),Windows_NT)
	@command -v node > /dev/null || which node > /dev/null
else
	@which python3 > /dev/null
endif

# We never overwrite version and release manually anymore, they get updated through CI.
update:
	@echo "We currently read version=$(version) release=$(release)"

$(ff_source_tarball) : version
	wget -O $(ff_source_tarball) https://archive.mozilla.org/pub/firefox/releases/$(version)/source/$(ff_source_tarball)

# Create initial directory structure before patches.
dir: $(ff_source_tarball)
	rm -rf $(argon_source_dir) $(ff_source_dir)
	tar xf $(ff_source_tarball)
	mv $(ff_source_dir) $(argon_source_dir)

patches:
	$(RUN_PYTHON) scripts/librewolf-patches.py $(version) $(release)

run:
	(cd $(argon_source_dir) && ./mach run)

build:
	(cd $(argon_source_dir) && ./mach build)

package:
	(cd $(argon_source_dir) && ./mach package)

flatpak:
	$(RUN_PYTHON) scripts/argon.py flatpak

win-portable:
	$(RUN_PYTHON) scripts/argon.py win-portable

win-installer:
	$(RUN_PYTHON) scripts/argon.py win-installer

verify-windows:
	$(RUN_PYTHON) scripts/argon.py verify-windows

src-tarball: all
	tar czf $(argon_source_tarball) $(argon_source_dir)
	sha256sum $(argon_source_tarball) > $(argon_source_tarball).sha256sum
	cat $(argon_source_tarball).sha256sum

clean:
	rm -rf $(argon_source_dir)

distclean: clean
	rm -rf $(ff_source_dir) $(ff_source_tarball) $(argon_source_tarball) $(argon_source_tarball).sha256sum

check-patchfail: dir
	./scripts/check-patchfail.sh $(argon_source_dir)

fetch-uBO:
	./scripts/update-ubo-assets.sh

include assets/testing.mk
