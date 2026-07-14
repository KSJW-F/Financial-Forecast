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

CHART_ANALYSIS_PROMPT = """你是期货图表早报分析助手。输入是「图表型早报」的结构化摘录（可能来自 OCR/价表/图注），没有完整文字观点。

请根据：标题品种、价格涨跌线索、库存/利润/成交变化、图注措辞，推断各品种短期趋势。

要求：
1. 只输出 JSON 数组，不要输出其他文字。
2. 字段：commodity / trend / confidence / summary
   - trend 只能是：看涨/看跌/震荡/偏多/偏空/中性/未知
   - summary 说明依据（如“现货连跌+利润走弱”），不超过 24 字
3. 黑色金属早报通常含：螺纹、热卷、铁矿、焦炭、焦煤、锰硅、硅铁、纯碱、玻璃 等，尽量按出现的品种分别给出判断。
4. 若只有报价表、完全看不出方向，可对主品种给「震荡」并降低 confidence。
5. 不要输出期货公司名作为 commodity。

结构化摘录:
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


def analyze_chart_with_llm(
    title: str,
    content: str,
    *,
    use_llm: bool = True,
) -> list[AnalysisResult]:
    """专用于图表型早报：先压缩上下文，再走 LLM；失败时用本地启发式兜底。"""
    from src.analyzer.chart_analyzer import build_chart_context, heuristic_chart_trends

    results: list[AnalysisResult] = []
    if use_llm and config.LLM_ENABLED:
        chart_content = build_chart_context(title, content)
        if config.LLM_PROVIDER == "wenhua":
            results = _analyze_with_wenhua(title, chart_content, prompt_template=CHART_ANALYSIS_PROMPT)
        elif config.LLM_PROVIDER == "openai" and config.OPENAI_API_KEY:
            results = _analyze_with_openai(title, chart_content, prompt_template=CHART_ANALYSIS_PROMPT)

    valid = [item for item in results if item.trend != "未知"]
    if valid:
        return valid

    # AI 不可用或返回未知时，用价表涨跌启发式
    return heuristic_chart_trends(title, content)


def _build_prompt(title: str, content: str, prompt_template: str = ANALYSIS_PROMPT) -> str:
    body = content[: config.LLM_MAX_CONTENT_CHARS]
    return prompt_template.format(title=title.strip(), content=body.strip())


def _normalize_results(title: str, content: str, results: list[AnalysisResult]) -> list[AnalysisResult]:
    normalized: list[AnalysisResult] = []
    for item in results:
        commodity = extract_commodity(title, content)
        if commodity == "综合":
            mapped = normalize_commodity(item.commodity)
            commodity = mapped or item.commodity or "综合"

        trend = item.trend if item.trend else "未知"
        if trend not in {"看涨", "看跌", "震荡", "偏多", "偏空", "中性", "未知"}:
            trend = "未知"

        normalized.append(
            AnalysisResult(
                commodity=commodity if commodity != "综合" else (normalize_commodity(item.commodity) or item.commodity or "综合"),
                trend=trend,
                confidence=item.confidence or "中",
                source="llm",
                summary=item.summary or "",
            )
        )
    return normalized


def _analyze_with_wenhua(
    title: str,
    content: str,
    prompt_template: str = ANALYSIS_PROMPT,
) -> list[AnalysisResult]:
    prompt = _build_prompt(title, content, prompt_template=prompt_template)
    try:
        raw = get_content(prompt)
        payload = extract_json_payload(raw)
        results = parse_llm_json(payload)
        return _normalize_results(title, content, results)
    except (WenhuaAIError, json.JSONDecodeError, KeyError, ValueError) as exc:
        logger.warning("文华 AI 分析失败: %s", exc)
        return []


def _analyze_with_openai(
    title: str,
    content: str,
    prompt_template: str = ANALYSIS_PROMPT,
) -> list[AnalysisResult]:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
    prompt = _build_prompt(title, content, prompt_template=prompt_template)

    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
    )
    raw = response.choices[0].message.content or "[]"
    payload = extract_json_payload(raw)
    results = parse_llm_json(payload)
    return _normalize_results(title, content, results)
