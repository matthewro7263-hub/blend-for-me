# Security Policy — Blend for me

## Security Overview

**Blend for me** operates a local TCP bridge between an MCP client and a running Blender instance.

### Security Principles

1. **Loopback Isolation**: The bridge server binds strictly to `127.0.0.1` and `::1`. It must never be exposed to public networks.
2. **Authenticated Token Pairing**: Pairing tokens can be generated and passed via `BLENDER_AGENT_TOKEN` environment variable.
3. **Privileged Code Execution**: `execute_python` and terminal operations are controlled via explicit permission policies (`BLENDER_AGENT_ALLOW_EXECUTE_PYTHON=1`).

## Reporting Vulnerabilities

If you discover a potential security issue, please report it via private disclosure or GitHub Security Advisories rather than public issue tracker.
