"""Make the MCP server package importable no matter where pytest is invoked from.

The tests live outside ``mcp_server/``, so pytest's rootdir inference cannot be
relied on to apply that project's ``pythonpath`` setting.
"""

from __future__ import annotations

import pathlib
import sys

SRC = pathlib.Path(__file__).resolve().parent.parent / "mcp_server" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs a running Blender bridge")
