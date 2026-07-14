"""Entry point: ``python -m phases_oss.multidim`` runs the stdio MCP server.

Also exposed as the ``phases-multidim`` console script (see pyproject).
"""

from __future__ import annotations

import sys

from .server import serve


def main(argv=None) -> int:
    # No arguments: the server speaks JSON-RPC over stdio. A future --help could
    # be added, but MCP clients spawn it with no args and pipe stdin/stdout.
    return serve()


if __name__ == "__main__":
    sys.exit(main())
