"""Phase hooks (PreToolUse / Stop / UserPromptSubmit) as standard-library ports.

Each submodule exposes a pure ``decide(payload, start)`` plus a thin ``main()``
that reads a JSON payload on stdin and writes a decision on stdout. They are a
discipline aid, not a security boundary (see ``common`` for the honest framing).
"""

from . import common, pre_tool_use, stop, user_prompt_submit

__all__ = ["common", "pre_tool_use", "stop", "user_prompt_submit"]
