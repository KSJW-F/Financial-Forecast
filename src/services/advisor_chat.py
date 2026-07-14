"""决策顾问：先检索库内共识与近期研报，再交给 LLM 回答。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import config
from src.analyzer.commodity import extract_commodity, normalize_commodity
from src.analyzer.wenhua_client import WenhuaAIError, get_content
from src.insights import compute_consensus
from src.models import Article, Prediction, SessionLocal

logger = logging.getLogger(__name__)

ADVISOR_PROMPT = """你是「Financial Forecast」期货决策顾问。只根据【结构化数据】作答，禁止编造机构观点。

【输出格式】四段，标题固定，不要增减，不要编号外的小标题：

结论：{stance_label}
依据：
1. <评分与主导观点，一句>
2. <多空家数与动量，一句>
3. <可选：1 条机构观点或风险点>
操作建议：<一句，含仓位或进出场>
风险提示：期货有风险，决策需谨慎。

【硬性禁止】
- 禁止 Markdown（**、##、```、*）
- 禁止任何英文：success、danger、warning、secondary、info、LLM、JSON、fallback
- 禁止把数据字段用「；」硬拼，如「控制仓位。；动量：平稳」
- 禁止复述「决策摘要」「倾向标签」「系统信号」原文；用自己的话写依据
- 标点只用中文句号、逗号、顿号、冒号

【判定参考】（结论已给出系统档位，一般直接采用）
- 加权评分 >= 0.55 → 谨慎偏多或积极偏多
- 0.12～0.55 → 震荡偏多
- -0.12～0.12 → 高抛低吸
- -0.55～-0.12 → 震荡偏空
- <= -0.55 → 谨慎偏空或积极偏空
- 「震荡」票多也不要一律观望，按评分给方向

【正确示例】
结论：震荡偏多
依据：
1. 时序加权评分 0.21，主导观点为震荡
2. 多头 47、空头 18，动量平稳
3. 近端机构多为震荡整理，少数看多
操作建议：回调轻仓试多，总仓位不超过三成，突破再加
风险提示：期货有风险，决策需谨慎。

用户问题：{question}
关注品种：{commodity}

【结构化数据】
{context}
"""


def _stance_label(insight) -> str:
    if not insight or insight.identified_reports <= 0:
        return "数据不足"
    score = float(insight.weighted_score)
    if score >= 0.85:
        return "积极偏多"
    if score >= 0.55:
        return "谨慎偏多"
    if score >= 0.12:
        return "震荡偏多"
    if score <= -0.85:
        return "积极偏空"
    if score <= -0.55:
        return "谨慎偏空"
    if score <= -0.12:
        return "震荡偏空"
    return "高抛低吸"


def _stance_brief(insight) -> str:
    """给 fallback / 调试用的短说明，不含英文 level。"""
    if not insight or insight.identified_reports <= 0:
        return "数据不足"
    dist = insight.distribution or {}
    bullish = sum(dist.get(k, 0) for k in ("看涨", "偏多"))
    bearish = sum(dist.get(k, 0) for k in ("看跌", "偏空"))
    label = _stance_label(insight)
    return (
        f"{label}（加权评分 {insight.weighted_score}，"
        f"多头 {bullish}，空头 {bearish}，动量 {insight.momentum}）"
    )


@dataclass
class AdvisorReply:
    ok: bool
    answer: str
    commodity: str
    context_used: str
    source: str  # llm | fallback


def _detect_commodity(question: str, preferred: str = "") -> str:
    if preferred:
        mapped = normalize_commodity(preferred) or preferred.strip()
        if mapped:
            return mapped
    mapped = extract_commodity(question, "")
    if mapped and mapped != "综合":
        return mapped
    for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", question):
        name = normalize_commodity(token)
        if name:
            return name
    return preferred or "综合"


def _format_distribution(distribution: dict[str, int]) -> str:
    if not distribution:
        return "无"
    order = ("看涨", "偏多", "震荡", "中性", "偏空", "看跌", "未知")
    parts = []
    for key in order:
        if key in distribution:
            parts.append(f"{key}{distribution[key]}篇")
    for key, value in distribution.items():
        if key not in order:
            parts.append(f"{key}{value}篇")
    return "、".join(parts)


def _build_context(session, commodity: str, date_from: str = "", date_to: str = "") -> str:
    parts: list[str] = []
    insight = compute_consensus(session, commodity, date_from, date_to)
    if insight and insight.identified_reports > 0:
        # 只给干净字段，不塞长决策文案 / 英文 level，避免模型拼接出「。；(success)」
        dist = insight.distribution or {}
        bullish = sum(dist.get(k, 0) for k in ("看涨", "偏多"))
        bearish = sum(dist.get(k, 0) for k in ("看跌", "偏空"))
        parts.append(
            f"品种：{insight.commodity}\n"
            f"统计区间：{insight.date_from or '全部'} 至 {insight.date_to or '全部'}\n"
            f"有效研报：{insight.identified_reports} 篇（共 {insight.total_reports} 篇）\n"
            f"时序加权评分：{insight.weighted_score}\n"
            f"系统档位：{_stance_label(insight)}\n"
            f"主导观点：{insight.consensus_trend}（占比 {insight.consensus_pct}%）\n"
            f"多头家数：{bullish}\n"
            f"空头家数：{bearish}\n"
            f"动量：{insight.momentum}\n"
            f"动量说明：{insight.momentum_detail}\n"
            f"持续性：{insight.persistence_label}\n"
            f"多头机构：{'、'.join(insight.bullish_brokers[:6]) or '无'}\n"
            f"空头机构：{'、'.join(insight.bearish_brokers[:6]) or '无'}\n"
            f"观点分布：{_format_distribution(dist)}"
        )
        if insight.broker_views:
            parts.append("近期机构观点：")
            for view in insight.broker_views[:6]:
                summary = (view.summary or "").replace("\n", " ").strip()[:60]
                line = f"{view.publish_date} {view.broker}：{view.trend}"
                if summary:
                    line += f"。{summary}"
                parts.append(line)
    else:
        rows = (
            session.query(Prediction, Article)
            .join(Article, Prediction.article_id == Article.id)
            .filter(Prediction.commodity == commodity)
            .filter(Prediction.trend != "未知")
            .order_by(Article.publish_date.desc(), Prediction.id.desc())
            .limit(10)
            .all()
        )
        if not rows:
            parts.append(f"库内暂无「{commodity}」的有效趋势预测。")
        else:
            parts.append(f"库内「{commodity}」近期预测：")
            for pred, article in rows:
                summary = (pred.summary or "").replace("\n", " ").strip()[:60]
                line = f"{article.publish_date} {article.broker}：{pred.trend}"
                if summary:
                    line += f"。{summary}"
                parts.append(line)
    return "\n".join(parts)[:5000]


def _fallback_answer(commodity: str, insight, context: str) -> str:
    if not insight or insight.identified_reports <= 0 or "暂无" in context:
        return (
            f"结论：数据不足\n"
            f"依据：\n1. 当前库内「{commodity}」有效研报不足\n"
            f"2. 建议上传相关研报或调整日期后再问\n"
            f"操作建议：暂不依据系统给出方向性仓位\n"
            f"风险提示：期货有风险，决策需谨慎。"
        )
    dist = insight.distribution or {}
    bullish = sum(dist.get(k, 0) for k in ("看涨", "偏多"))
    bearish = sum(dist.get(k, 0) for k in ("看跌", "偏空"))
    label = _stance_label(insight)
    action = {
        "积极偏多": "可逢回落布局多单，仓位可略积极但仍设止损",
        "谨慎偏多": "轻仓逢低试多，仓位建议不超过三成",
        "震荡偏多": "区间偏多：回调低吸，突破再加，控制总仓位",
        "高抛低吸": "适合高抛低吸或观望突破，不宜单边重仓",
        "震荡偏空": "区间偏空：反弹减仓或轻仓试空，避免左侧抄底",
        "谨慎偏空": "注意下行风险，反弹谨慎，仓位宜轻",
        "积极偏空": "偏空思路为主，反弹减仓，严格止损",
    }.get(label, "按评分与多空分布轻仓试探")
    return (
        f"结论：{label}\n"
        f"依据：\n"
        f"1. 时序加权评分 {insight.weighted_score}，主导观点 {insight.consensus_trend}\n"
        f"2. 多头 {bullish} 家，空头 {bearish} 家，动量 {insight.momentum}\n"
        f"3. {_format_distribution(dist)}\n"
        f"操作建议：{action}\n"
        f"风险提示：期货有风险，决策需谨慎。"
    )


def _call_llm(prompt: str) -> str:
    if not config.LLM_ENABLED:
        raise WenhuaAIError("LLM 未启用")

    if config.LLM_PROVIDER == "wenhua":
        return get_content(prompt)

    if config.LLM_PROVIDER == "openai" and config.OPENAI_API_KEY:
        from openai import OpenAI

        client = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return (response.choices[0].message.content or "").strip()

    raise WenhuaAIError("未配置可用的 LLM Provider")


def ask_advisor(
    question: str,
    *,
    commodity: str = "",
    date_from: str = "",
    date_to: str = "",
) -> AdvisorReply:
    question = (question or "").strip()
    if not question:
        return AdvisorReply(False, "请输入问题", "", "", "fallback")

    session = SessionLocal()
    try:
        detected = _detect_commodity(question, commodity)
        insight = compute_consensus(session, detected, date_from, date_to)
        stance_label = _stance_label(insight)
        context = _build_context(session, detected, date_from, date_to)
        prompt = ADVISOR_PROMPT.format(
            question=question[:800],
            commodity=detected,
            stance_label=stance_label,
            context=context or "无数据",
        )
        try:
            answer = _call_llm(prompt)
            if answer:
                return AdvisorReply(
                    True,
                    normalize_advisor_output(answer),
                    detected,
                    context,
                    "llm",
                )
        except Exception as exc:
            logger.warning("顾问 LLM 失败，回退结构化答复: %s", exc)

        return AdvisorReply(
            True,
            normalize_advisor_output(_fallback_answer(detected, insight, context)),
            detected,
            context,
            "fallback",
        )
    finally:
        session.close()


def strip_markdown(text: str) -> str:
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n")
    cleaned = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", ""), cleaned)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", cleaned)
    cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"^>\s?", "", cleaned, flags=re.M)
    cleaned = cleaned.replace("**", "").replace("__", "")
    return cleaned.strip()


def normalize_advisor_output(text: str) -> str:
    """清洗模型/回退文本中的异常拼接与内部标签。"""
    cleaned = strip_markdown(text)
    # 去掉英文状态码（含括号、中文括号）
    cleaned = re.sub(
        r"[（(]\s*(success|danger|warning|secondary|info|fallback|llm)\s*[)）]",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\b(success|danger|warning|secondary|info|fallback|llm)\b",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"LLM\s*暂不可用", "", cleaned)
    cleaned = re.sub(r"系统数据摘录（.*?）", "", cleaned)
    cleaned = re.sub(r"系统数据摘要（.*?）", "", cleaned)
    # 修复「。；」「；动量」等拼接
    cleaned = re.sub(r"[。．]\s*[；;]\s*", "。", cleaned)
    cleaned = re.sub(r"[；;]\s*动量\s*[：:]", "\n动量：", cleaned)
    cleaned = re.sub(r"[；;]\s*系统信号\s*[：:]", "。", cleaned)
    cleaned = re.sub(r"决策信号\s*[：:]\s*", "", cleaned)
    cleaned = re.sub(r"建议档位\s*[：:]\s*", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"。{2,}", "。", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[；;]{2,}", "。", cleaned)
    # 行尾残留空括号
    cleaned = re.sub(r"[（(]\s*[)）]", "", cleaned)
    return cleaned.strip()
