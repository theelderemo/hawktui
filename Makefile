# Version lives in hawktui/hawktui.py (__version__) and is read by hatchling.

PYTHON       ?= python3
VERSION_FILE := hawktui/hawktui.py
VERSION      := $(shell grep -oP '__version__\s*=\s*"\K[^"]+' $(VERSION_FILE))

.PHONY: help version run build release-patch release-minor release-major

help:
	@echo "HawkTUI v$(VERSION)"
	@echo ""
	@echo "  make run             run from source"
	@echo "  make build           build standalone binary into ~/.local/bin"
	@echo "  make version         print current version"
	@echo "  make release-patch   $(VERSION) -> next patch, tag, push"
	@echo "  make release-minor   $(VERSION) -> next minor, tag, push"
	@echo "  make release-major   $(VERSION) -> next major, tag, push"

version:
	@echo $(VERSION)

run:
	$(PYTHON) hawktui/hawktui.py

build:
	cd hawktui && $(PYTHON) build.py

release-patch: PART := patch
release-minor: PART := minor
release-major: PART := major
release-patch release-minor release-major:
	@git diff --quiet && git diff --cached --quiet || { \
		echo "working tree is dirty — commit or stash first"; exit 1; }
	@new=$$($(PYTHON) -c "i={'major':0,'minor':1,'patch':2}['$(PART)']; \
v=[int(x) for x in '$(VERSION)'.split('.')]; v[i]+=1; v[i+1:]=[0]*(2-i); \
print('.'.join(map(str,v)))"); \
	echo "bumping $(VERSION) -> $$new"; \
	sed -i 's/^__version__ = ".*"/__version__ = "'$$new'"/' $(VERSION_FILE); \
	git add $(VERSION_FILE); \
	git commit -m "release: v$$new"; \
	git tag -a "v$$new" -m "HawkTUI v$$new"; \
	git push origin HEAD --follow-tags; \
	echo "released v$$new"
