from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importer.opencode_transcript import (
    coerce_messages,
    message_prompt_text,
    normalize_message,
)


def test_normalize_message_from_opencode_v2_info_role():
    normalized = normalize_message(
        {
            "info": {
                "id": "msg-1",
                "role": "assistant",
                "providerID": "openai",
                "modelID": "gpt-test",
            },
            "parts": [
                {"type": "tool", "tool": "read", "state": {"status": "completed", "input": {"path": "a"}, "output": "ok"}},
                {"type": "text", "text": "done"},
            ],
        }
    )
    assert normalized is not None
    assert normalized["role"] == "assistant"
    assert normalized["providerId"] == "openai"
    assert len(normalized["parts"]) == 2


def test_coerce_messages_accepts_normalized_bundle_format():
    session = {
        "messagesFormat": "opencode-v2-normalized",
        "messages": [
            {"role": "user", "messageId": "u1", "parts": [{"type": "text", "text": "Analyze"}]},
            {
                "role": "assistant",
                "messageId": "a1",
                "providerId": "openai",
                "modelId": "gpt-test",
                "parts": [{"type": "text", "text": "package Demo {}"}],
            },
        ],
    }
    messages = coerce_messages(session)
    assert len(messages) == 2
    assert message_prompt_text(messages[0]) == "Analyze"
