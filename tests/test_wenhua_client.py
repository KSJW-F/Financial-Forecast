from unittest.mock import MagicMock

import pytest
import requests

from src.analyzer.wenhua_client import WenhuaAIError, _parse_sse_text, _read_stream_safely, get_content


def test_parse_sse_text():
    raw = '''event:message
data: {"choices":[{"delta":{"content":"["},"finish_reason":null}]}
data: {"choices":[{"delta":{"content":"]"},"finish_reason":"stop"}]}'''
    chunks, finished = _parse_sse_text(raw)
    assert "".join(chunks) == "[]"
    assert finished is True


def test_read_stream_safely_on_chunk_error():
    response = MagicMock()
    response.iter_content.return_value = [b"data: ", b'{"choices":[]}']
    response.close = MagicMock()

    def broken_iter(*args, **kwargs):
        yield b"event:message\n"
        yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n'
        raise requests.exceptions.ChunkedEncodingError("broken")

    response.iter_content.side_effect = broken_iter
    text = _read_stream_safely(response)
    assert "ok" in text


def test_extract_json_payload_from_markdown():
    from src.analyzer.wenhua_client import extract_json_payload

    raw = """```json
[{"commodity": "热卷", "trend": "偏空"}]
```"""
    assert "热卷" in extract_json_payload(raw)
