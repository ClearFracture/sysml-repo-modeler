from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sysml_backend.services.opencode_transcript import (
    message_role,
    normalize_opencode_message,
    tool_part_payload,
)


def test_message_role_reads_info_role():
    assert (
        message_role({"info": {"role": "assistant"}, "parts": []})
        == "assistant"
    )


def test_normalize_opencode_message_preserves_tool_parts():
    normalized = normalize_opencode_message(
        {
            "info": {"id": "m1", "role": "assistant", "providerID": "openai", "modelID": "gpt"},
            "parts": [
                {
                    "type": "tool",
                    "tool": "grep",
                    "state": {
                        "status": "completed",
                        "input": {"pattern": "main"},
                        "output": "found",
                    },
                }
            ],
        }
    )
    assert normalized is not None
    payload = tool_part_payload(normalized["parts"][0])
    assert payload["tool"] == "grep"
    assert payload["output"] == "found"
