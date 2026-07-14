"""Bundled, autonomous Multidim MCP server for phases-oss.

Multidim is shipped **with** phases-oss but is **not fused** into the ``/phases``
engine: it is a separate component with its own MCP server, its own storage on a
dedicated path, its own contract and its own tests. The coupling is over stdio:
:mod:`phases_oss.multidim.client` spawns the server and speaks the protocol,
never importing the server internals to bypass it.

Public surface:

* :func:`phases_oss.multidim.server.serve` -- run the stdio server;
* :func:`phases_oss.multidim.client.prepare` -- run an analysis for a phase;
* :func:`phases_oss.multidim.paths.store_path` -- the dedicated store path;
* :mod:`phases_oss.multidim.store` / :mod:`phases_oss.multidim.base_contexts`.
"""

from __future__ import annotations

from . import base_contexts, client, frames, paths, server, store, validate

__all__ = ["server", "store", "paths", "base_contexts", "frames", "validate", "client"]
