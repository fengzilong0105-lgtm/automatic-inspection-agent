import json

from agent.feishu.client import _error_hint


def test_error_hint_permission():
    hint = _error_hint(99991663, "permission denied")
    assert "im:message" in hint


def test_feishu_text_payload_format():
    content = json.dumps({"text": "hello"}, ensure_ascii=False)
    assert "hello" in content
