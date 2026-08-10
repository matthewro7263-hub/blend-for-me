ifeq ($(OS),Windows_NT)
    DEFAULT_BLENDER := C:/Program Files/Blender Foundation/Blender 5.2/blender.exe
else
    UNAME_S := $(shell uname -s)
    ifeq ($(UNAME_S),Darwin)
        DEFAULT_BLENDER := /Applications/Blender.app/Contents/MacOS/Blender
    else
        DEFAULT_BLENDER := $(shell which blender 2>/dev/null || echo /usr/bin/blender)
    endif
endif

BLENDER ?= $(DEFAULT_BLENDER)
PORT    ?= 9876
TESTPORT?= 9899
REPO    := $(shell pwd)

.PHONY: help build-ext install-ext run-server test test-unit smoke demo probe clean skill-inventory skill-check

help:
	@echo "build-ext    Build dist/blender_agent_mcp-*.zip"
	@echo "install-ext  Build, then install + enable the extension in Blender"
	@echo "run-server   Run the MCP server on stdio (what Claude Code launches)"
	@echo "test         Unit tests + headless integration smoke"
	@echo "smoke        Headless integration smoke only"
	@echo "demo         GUI acceptance demo (needs Blender running with the bridge)"
	@echo "probe        Re-verify Blender/MCP APIs after an upgrade"
	@echo "skill-inventory  Regenerate the Agent Skill's tool inventory"
	@echo "skill-check      Fail if the skill inventory is stale"

build-ext:
	python3 scripts/build_extension.py

install-ext: build-ext
	python3 scripts/install_extension.py --blender "$(BLENDER)"

run-server:
	uv run --directory mcp_server blender-agent-mcp

test-unit:
	cd mcp_server && uv run --extra dev pytest ../tests -q -m "not integration"

smoke:
	$(BLENDER) --background --factory-startup --python tests/headless_smoke.py

test: test-unit smoke

demo:
	$(BLENDER) --python tests/gui_activity_demo.py

probe:
	$(BLENDER) --background --factory-startup --python scripts/probe_api.py

skill-inventory:
	cd mcp_server && uv run python ../skills/blender-agent-mcp/scripts/gen_tool_inventory.py

skill-check:
	cd mcp_server && uv run python ../skills/blender-agent-mcp/scripts/gen_tool_inventory.py --check

clean:
	rm -rf dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
