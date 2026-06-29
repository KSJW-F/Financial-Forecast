from __future__ import annotations

import json
import logging
import re
import time

import requests

import config

logger = logging.getLogger(__name__)


class WenhuaAIError(RuntimeError):
    pass


def get_content(prompt: str, timeout: int | None = None) -> str:
    """调用文华 AI 接口，聚合 SSE 流式响应为完整文本（含重试与断流容错）。"""
    timeout = timeout or config.LLM_TIMEOUT
    last_error: Exception | None = None

    for attempt in range(1, config.LLM_RETRY_COUNT + 1):
        try:
            text = _request_once(prompt, timeout)
            if text:
                return text
        except WenhuaAIError as exc:
            last_error = exc
            logger.warning("文华 AI 第 %s 次调用失败: %s", attempt, exc)

        if attempt < config.LLM_RETRY_COUNT:
            time.sleep(config.LLM_RETRY_DELAY * attempt)

    raise WenhuaAIError(f"接口调用失败（已重试 {config.LLM_RETRY_COUNT} 次）: {last_error}")


def _request_once(prompt: str, timeout: int) -> str:
    try:
        response = requests.post(
            config.WENHUA_AI_URL,
            json={"content": prompt},
            stream=True,
            timeout=(10, timeout),
            headers={
                "Accept": "text/event-stream",
                "Connection": "close",
            },
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise WenhuaAIError(f"请求失败: {exc}") from exc

    raw_text = _read_stream_safely(response)
    chunks, finished = _parse_sse_text(raw_text)

    text = "".join(chunks).strip()
    if text:
        return text

    if finished:
        raise WenhuaAIError("接口返回为空")

    raise WenhuaAIError("未能从 SSE 响应中解析出内容")


def _read_stream_safely(response: requests.Response) -> str:
    """读取 SSE 原始文本；连接提前断开时保留已收到的数据。"""
    parts: list[bytes] = []

    try:
        for chunk in response.iter_content(chunk_size=4096, decode_unicode=False):
            if chunk:
                parts.append(chunk)
    except (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.ReadTimeout,
    ) as exc:
        logger.warning("SSE 流传输中断，尝试使用已接收数据: %s", exc)
    finally:
        response.close()

    if not parts:
        return ""

    return b"".join(parts).decode("utf-8", errors="ignore")


def _parse_sse_text(raw_text: str) -> tuple[list[str], bool]:
    chunks: list[str] = []
    finished = False

    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("event:"):
            continue
        if not line.startswith("data:"):
            continue

        payload = line[5:].strip()
        if payload in {"[DONE]", "DONE"}:
            finished = True
            break

        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue

        for choice in event.get("choices", []):
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                chunks.append(content)

            if choice.get("finish_reason") == "stop":
                finished = True

    return chunks, finished


def extract_json_payload(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()

    array_match = re.search(r"\[[\s\S]*\]", raw)
    if array_match:
        return array_match.group(0)

    object_match = re.search(r"\{[\s\S]*\}", raw)
    if object_match:
        return object_match.group(0)

    return raw
