# Contributing to Blend for me

Thank you for contributing to **Blend for me**!

## Development Setup

1. Prerequisites:
   - Python 3.11+
   - Blender 4.2+ (Blender 5.2 LTS recommended)
   - `uv` package manager

2. Installing dependencies & running tests:
   ```bash
   make test-unit
   make smoke
   make test
   ```

3. Building the extension:
   ```bash
   make build-ext
   ```

## Pull Request Guidelines

- Ensure `make test` and `make skill-check` pass before submitting PRs.
- Keep stable internal identifiers (`blender_agent_mcp`, `blender-agent-mcp`) intact.
- Follow conventional commit messages (`feat: ...`, `fix: ...`, `docs: ...`).
