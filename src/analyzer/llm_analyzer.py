from __future__ import annotations

import json
import logging

import config
from src.analyzer.commodity import extract_commodity, normalize_commodity
from src.analyzer.rule_analyzer import AnalysisResult, parse_llm_json
from src.analyzer.wenhua_client import WenhuaAIError, extract_json_payload, get_content

logger = logging.getLogger(__name__)

ANALYSIS_PROMPT = """你是专业的期货研报分析助手。请阅读以下研报标题和正文，提取期货品种及后续走势判断。

要求：
1. 只输出 JSON 数组，不要输出任何其他文字。
2. 每个元素字段：
   - commodity: 标准品种名（如 热卷、螺纹、甲醇、PTA、原油、股指期权、国债 等，不要输出期货公司名）
   - trend: 只能是 看涨/看跌/震荡/偏多/偏空/中性/未知
   - confidence: 高/中/低
   - summary: 一句话说明判断依据（20字以内）
3. 若提到“操作建议:短线偏空”等，trend 应对应为 偏空。
4. 若无法判断趋势，trend 填 未知。
5. 若正文为空或仅为图片，请根据标题中的品种代码推断 commodity（如 MA=甲醇、PP=聚丙烯、L=聚乙烯）。

标题: {title}

正文:
{content}
"""


def analyze_with_llm(title: str, content: str) -> list[AnalysisResult]:
    if not config.LLM_ENABLED:
        return []

    if config.LLM_PROVIDER == "wenhua":
        return _analyze_with_wenhua(title, content)
    if config.LLM_PROVIDER == "openai" and config.OPENAI_API_KEY:
        return _analyze_with_openai(title, content)

    return []


def _build_prompt(title: str, content: str) -> str:
    body = content[: config.LLM_MAX_CONTENT_CHARS]
    return ANALYSIS_PROMPT.format(title=title.strip(), content=body.strip())


def _normalize_results(title: str, content: str, results: list[AnalysisResult]) -> list[AnalysisResult]:
    normalized: list[AnalysisResult] = []
    for item in results:
        commodity = extract_commodity(title, content)
        if commodity == "综合":
            mapped = normalize_commodity(item.commodity)
            commodity = mapped or item.commodity or "综合"

        normalized.append(
            AnalysisResult(
                commodity=commodity,
                trend=item.trend if item.trend else "未知",
                confidence=item.confidence or "中",
                source="llm",
                summary=item.summary or "",
            )
        )
    return normalized


def _analyze_with_wenhua(title: str, content: str) -> list[AnalysisResult]:
    prompt = _build_prompt(title, content)
    try:
        raw = get_content(prompt)
        payload = extract_json_payload(raw)
        results = parse_llm_json(payload)
        return _normalize_results(title, content, results)
    except (WenhuaAIError, json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("文华 AI 分析失败: %s", exc)
        return []


def _analyze_with_openai(title: str, content: str) -> list[AnalysisResult]:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
    prompt = _build_prompt(title, content)

    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    raw = response.choices[0].message.content or "[]"
    payload = extract_json_payload(raw)
    results = parse_llm_json(payload)
    return _normalize_results(title, content, results)
