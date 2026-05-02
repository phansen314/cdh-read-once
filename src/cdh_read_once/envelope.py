from __future__ import annotations

from typing import Literal

Decision = Literal["allow", "deny", "ask"]


def build_pre_tool_use_envelope(decision: Decision, reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        },
    }
