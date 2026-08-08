BLENDER ?= /Applications/Blender.app/Contents/MacOS/Blender
PORT    ?= 9876
TESTPORT?= 9899
REPO    := $(shell pwd)

.PHONY: help build-ext install-ext run-server test test-unit smoke demo probe clean

help:
	@echo "build-ext    Build dist/blender_agent_mcp-*.zip"
	@echo "install-ext  Build, then install + enable the extension in Blender"
	@echo "run-server   Run the MCP server on stdio (what Claude Code launches)"
	@echo "test         Unit tests + headless integration smoke"
	@echo "smoke        Headless integration smoke only"
	@echo "demo         GUI acceptance demo (needs Blender running with the bridge)"
	@echo "probe        Re-verify Blender/MCP APIs after an upgrade"

build-ext:
	python3 scripts/build_extension.py

install-ext: build-ext
	$(BLENDER) --background --factory-startup --python scripts/install_extension.py

run-server:
	uv run --directory mcp_server blender-agent-mcp

test-unit:
	cd mcp_server && uv run --extra dev pytest ../tests -q -m "not integration"

smoke:
	$(BLENDER) --background --factory-startup --python tests/headless_smoke.py

test: test-unit smoke

demo:
	cd mcp_server && uv run python ../tests/gui_demo.py

probe:
	$(BLENDER) --background --factory-startup --python scripts/probe_api.py

clean:
	rm -rf dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
