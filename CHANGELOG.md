# Changelog — Blend for me

All notable changes to this project will be documented in this file.

## [0.0.1-beta] - 2026-08-10

### Added
- Authenticated pairing token verification and session ID tracking.
- Node graph introspection tool (`shading.describe_node`).
- Live TCP socket E2E pytest suite (`test_socket_e2e.py`).
- `image_with_metadata` helper preserving resolution, engine, and diagnostic text with MCP image content blocks.
- Declarative effect metadata and strict parameter validation across all bridge commands.

### Fixed
- Atomic custom property rollback and material slot assignment failure handling.
- UV mode and element selection context restoration context manager.
- Cycles render engine activation in factory startup mode.
- Memory-safe render diagnostics using `array.array('f')` and `foreach_get`.
- `execute_python` process protection for `SystemExit`.
- `make demo` and `make install-ext` targets in Makefile.
