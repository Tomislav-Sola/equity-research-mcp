"""FastMCP server entrypoint.

Phase 1 ships a single `health` tool to prove the server starts and
registers tools. Real tools land in Phases 2–5.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import __version__

mcp = FastMCP("equity-research-mcp")


# Diagnostic tool — does NOT count toward the 6 research tools frozen
# for v0.1 (see CLAUDE.md "Tools surface frozen at 6"). Revisit at the
# v0.1.0 release: keep as a diagnostic, or remove if the 6 tools cover
# the smoke-test need.
@mcp.tool()
def health() -> dict[str, str]:
    """Return server status. No external calls."""
    return {"status": "ok", "version": __version__}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
