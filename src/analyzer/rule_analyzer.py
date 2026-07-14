from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.analyzer.commodity import extract_commodity

TREND_PATTERNS: list[tuple[str, str]] = [
    (r"核心观点[:：]\s*(中性|偏多|偏空|震荡|看涨|看跌|看多|看空)", "direct"),
    (r"(?:综合)?观点[:：]\s*(中性|偏多|偏空|震荡|看涨|看跌|看多|看空)", "direct"),
    (r"操作上[，,：:]\s*建议暂时观望[，,。]?\s*短线空头思路", "偏空"),
    (r"操作上[，,：:]\s*建议暂时观望[，,。]?\s*短线多头思路", "偏多"),
    (r"短线空头思路", "偏空"),
    (r"短线多头思路", "偏多"),
    (r"空头氛围升温", "偏空"),
    (r"多头氛围升温", "偏多"),
    (r"继续维持弱势|维持弱势|继续承压|震荡偏弱|偏弱状态", "偏空"),
    (r"继续维持强势|维持强势|震荡偏强|偏强运行", "偏多"),
    (r"建议观望|建议多多观望|观望为宜|建议保持观望|建议.{0,8}暂时观望|暂时观望", "震荡"),
    (r"窄幅震荡|低位震荡|维持震荡", "震荡"),
    (r"偏稳波动(?:节奏)?(?:对待)?", "震荡"),
    (r"强势震荡|温和反弹|维持强势震荡", "偏多"),
    (r"偏弱震荡|偏弱运行|延续偏弱", "偏空"),
    (r"下行压力较大", "偏空"),
    (r"短期目标上移|创阶段性新高|强势爆发", "偏多"),
    (r"成本坍塌难以逆转", "偏空"),
    (r"总体偏弱|现货报价总体偏弱", "偏空"),
    (r"总体偏强|现货报价总体偏强", "偏多"),
    (r"承压(?:走低|下行|回落)?", "偏空"),
    (r"偏空(?:运转|参与)?", "偏空"),
    (r"回调偏多思路", "偏多"),
    (r"回调偏空思路", "偏空"),
    (r"建议继续回调偏多思路", "偏多"),
    (r"建议以.*?偏空思路为主", "偏空"),
    (r"建议以.*?偏多思路为主", "偏多"),
    (r"以短线交易为主|短线交易为主", "震荡"),
    (r"操作建议[:：]\s*IH强于IM", "偏多"),
    (r"操作建议[:：]\s*IM强于IH", "偏空"),
    (r"对黄金构成支撑|提振了黄金", "偏多"),
    (r"市场看空情绪浓厚", "偏空"),
    (r"构成利空", "偏空"),
    (r"构成利好", "偏多"),
    (r"引起恐慌", "偏空"),
    (r"全球经济向上的趋势|经济向上的趋势并未", "偏多"),
    (r"实质性影响远低于", "偏多"),
    (r"操作建议[:：]", "strategy_marker"),
    (r"操作上[，,：:]", "strategy_marker"),
    (r"操作上建议", "strategy_marker"),
    (r"【交易策略】", "strategy_marker"),
    (r"【操作策略】", "strategy_marker"),
    (r"交易策略[:：]", "strategy_marker"),
    (r"策略建议[:：]", "strategy_marker"),
    (r"市场情绪(?:明显)?(?:走弱|恶化|降温|转弱)", "偏空"),
    (r"市场情绪(?:明显)?(?:好转|改善|回暖|升温)", "偏多"),
    (r"易涨难跌", "偏多"),
    (r"易跌难涨", "偏空"),
    (r"布局多单(?:为主)?", "偏多"),
    (r"布局空单(?:为主)?", "偏空"),
    (r"逢(?:价格)?回落(?:布局)?多单", "偏多"),
    (r"逢(?:价格)?反弹(?:布局)?空单", "偏空"),
    (r"操作建议[:：]\s*([^\n。]{2,40})", "explicit"),
    (r"操作上[，,：:]\s*([^\n。]{2,40})", "explicit"),
    (r"操作上建议\s*([^\n。]{2,40})", "explicit"),
    (r"策略建议[:：]\s*([^\n。]{2,80})", "explicit"),
    (r"交易策略[:：]\s*([^\n。]{2,40})", "explicit"),
    (r"短期以观望为主", "震荡"),
    (r"建议暂时观望", "震荡"),
    (r"以观望为主", "震荡"),
    (r"([^。\n]{1,8})以震荡的观点", "震荡"),
    (r"以震荡(?:的)?观点(?:看待)?", "震荡"),
    (r"观点[:：][^。\n]{0,24}震荡", "震荡"),
    (r"观点[:：][^。\n]{0,24}看多", "看涨"),
    (r"观点[:：][^。\n]{0,24}看空", "看跌"),
    (r"当前方向看震荡", "震荡"),
    (r"长期方向看多", "看涨"),
    (r"长期方向看空", "看跌"),
    (r"短线偏空", "偏空"),
    (r"短线偏多", "偏多"),
    (r"中期偏空", "偏空"),
    (r"中期偏多", "偏多"),
    (r"长期偏空", "偏空"),
    (r"长期偏多", "偏多"),
    (r"偏空参与", "偏空"),
    (r"偏多参与", "偏多"),
    (r"维持(?:偏空|空)头", "偏空"),
    (r"维持(?:偏多|多)头", "偏多"),
    (r"谨慎(?:看空|偏空)", "偏空"),
    (r"谨慎(?:看多|偏多)", "偏多"),
    (r"看空", "看跌"),
    (r"看涨", "看涨"),
    (r"看跌", "看跌"),
    (r"看多", "看涨"),
    (r"震荡(?:看待|运行|为主|格局|整理|区间)", "震荡"),
    (r"中性看待", "中性"),
    (r"方向看震荡", "震荡"),
    (r"价格(?:表现)?(?:持续)?偏弱", "偏空"),
    (r"价格(?:表现)?(?:持续)?偏强", "偏多"),
    (r"延续(?:反弹|上行)", "偏多"),
    (r"延续(?:回落|下行)", "偏空"),
]

SECTION_COMMODITIES = (
    "锰硅", "硅铁", "热卷", "螺纹", "铁矿", "焦炭", "焦煤", "沪铜", "沪铝", "沪锌",
    "沪镍", "原油", "燃油", "沥青", "PTA", "甲醇", "玻璃", "纯碱", "豆粕", "菜粕",
    "棕榈油", "豆油", "玉米", "棉花", "白糖", "橡胶", "生猪", "鸡蛋", "白银", "黄金",
    "碳酸锂", "工业硅", "股指", "油脂", "钢材",
)


@dataclass
class AnalysisResult:
    commodity: str
    trend: str
    confidence: str
    source: str
    summary: str
    unknown_reason: str = ""


def analyze_with_rules(title: str, content: str) -> list[AnalysisResult]:
    if not content or len(content.strip()) < 10:
        # 标题里也可能带观点，如「尿素早评：需求阶段性放缓」
        title_trend = _extract_title_trend(title)
        if title_trend:
            return [title_trend]
        return []

    sections = _split_sections(title, content)
    results: list[AnalysisResult] = []

    for section_title, section_text in sections:
        commodity = extract_commodity(section_title, section_text)
        if commodity == "综合":
            commodity = extract_commodity(title, section_text)

        trend, summary, confidence = _extract_trend(f"{section_title}\n{section_text}")
        if trend == "未知":
            continue

        results.append(
            AnalysisResult(
                commodity=commodity,
                trend=trend,
                confidence=confidence,
                source="rule",
                summary=summary,
            )
        )

    if results:
        return _dedupe_results(results)

    commodity = extract_commodity(title, content)
    trend, summary, confidence = _extract_trend(f"{title}\n{content}")
    if trend != "未知":
        return [
            AnalysisResult(
                commodity=commodity,
                trend=trend,
                confidence=confidence,
                source="rule",
                summary=summary,
            )
        ]

    title_trend = _extract_title_trend(title)
    if title_trend:
        return [title_trend]
    return []


def _extract_title_trend(title: str) -> AnalysisResult | None:
    if not title:
        return None
    # 尿素早评20250429：需求阶段性放缓 / 豆粕：美豆收跌
    # 中衍：金银期货报告——工业金属压力减弱给黄金支撑
    phrase = ""
    match = re.search(r"[：:—-]([^：:—-]{2,40})$", title.strip())
    if match:
        phrase = match.group(1).strip()
    else:
        phrase = title.strip()
    bearish = len(re.findall(r"放缓|走弱|偏弱|收跌|下跌|承压|回落|降温|压力", phrase))
    bullish = len(
        re.findall(r"走强|偏强|收涨|上涨|回暖|改善|好转|反弹|支撑|提振", phrase)
    )
    # 「压力减弱给黄金支撑」：支撑优先
    if "支撑" in phrase and "压力减弱" in phrase:
        bullish += 2
        bearish = max(0, bearish - 1)
    if bearish == 0 and bullish == 0:
        return None
    trend = "偏空" if bearish > bullish else "偏多"
    return AnalysisResult(
        commodity=extract_commodity(title, ""),
        trend=trend,
        confidence="中",
        source="rule",
        summary=phrase[:80],
    )


def _split_sections(title: str, content: str) -> list[tuple[str, str]]:
    if len(content) < 800:
        return [(title, content)]

    parts: list[tuple[int, str]] = []
    for name in SECTION_COMMODITIES:
        for match in re.finditer(rf"{re.escape(name)}方面[，,:：]?", content):
            parts.append((match.start(), name))
        for match in re.finditer(rf"【{re.escape(name)}】", content):
            parts.append((match.start(), name))

    # 中金财富 / 华龙等：【宏观策略】【股指期货】【贵金属】【黄金】
    for match in re.finditer(r"【([^【】]{2,12})】", content):
        name = match.group(1).strip()
        if name in {"宏观策略", "宏观"}:
            parts.append((match.start(), "宏观"))
        elif name in {"股指期货", "股指", "股指早评"}:
            parts.append((match.start(), "股指"))
        elif name in {"贵金属", "贵金属早评"}:
            parts.append((match.start(), "黄金"))
        elif name in {"黄金"}:
            parts.append((match.start(), "黄金"))
        elif name in {"白银"}:
            parts.append((match.start(), "白银"))
        elif name in {"铜"}:
            parts.append((match.start(), "沪铜"))
        elif name in {"铝"}:
            parts.append((match.start(), "沪铝"))
        elif name in {"豆类基本面", "大豆"}:
            parts.append((match.start(), "豆粕"))
        elif name in {"豆粕菜粕"}:
            parts.append((match.start(), "豆粕"))
        elif name in SECTION_COMMODITIES:
            parts.append((match.start(), name))

    # 华龙：股指早评 / 贵金属早评 / 聚乙烯 等小标题
    for match in re.finditer(
        r"(?:^|\n)\s*((?:股指|贵金属|黄金|白银|聚乙烯|聚丙烯|PP|PE|原油|螺纹|铁矿)早评)",
        content,
    ):
        name = match.group(1).replace("早评", "")
        parts.append((match.start(), name if name != "贵金属" else "黄金"))

    if len(parts) < 2:
        return [(title, content)]

    parts = sorted({(pos, name) for pos, name in parts}, key=lambda item: item[0])
    sections: list[tuple[str, str]] = []
    for index, (start, name) in enumerate(parts):
        end = parts[index + 1][0] if index + 1 < len(parts) else len(content)
        chunk = content[start:end].strip()
        if len(chunk) > 40:
            sections.append((f"{name}方面", chunk))

    return sections or [(title, content)]


def _dedupe_results(results: list[AnalysisResult]) -> list[AnalysisResult]:
    seen: set[tuple[str, str]] = set()
    unique: list[AnalysisResult] = []
    for item in results:
        key = (item.commodity, item.trend)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


DIMENSION_TREND_RE = re.compile(
    r"[；;，,]\s*(中性|偏多|偏空|震荡|看涨|看跌|看多|看空)\s*(?:$|\n|。|；|;)",
    re.MULTILINE,
)


def _extract_trend(text: str) -> tuple[str, str, str]:
    strategy_trend = _extract_strategy_trend(text)
    if strategy_trend:
        return strategy_trend

    for pattern, label in TREND_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        if label == "strategy_marker":
            strategy_trend = _extract_strategy_trend(text)
            if strategy_trend:
                return strategy_trend
            continue
        if label == "explicit":
            phrase = match.group(1).strip()
            mapped = _map_phrase_to_trend(phrase)
            if mapped == "未知":
                continue
            return mapped, phrase, "高"
        if label == "direct":
            word = match.group(1).strip()
            mapped = _map_phrase_to_trend(word)
            if mapped != "未知":
                return mapped, match.group(0), "高"
            continue
        return label, match.group(0), "高"

    dimension_trend, dimension_summary = _extract_dimension_consensus(text)
    if dimension_trend != "未知":
        return dimension_trend, dimension_summary, "高"

    bullish = len(re.findall(r"上涨|走强|反弹|上行|偏多|看多|好转|改善|收涨", text))
    bearish = len(re.findall(r"下跌|走弱|回落|下行|承压|偏空|看空|偏弱|收跌", text))
    if bullish > bearish + 1:
        return "偏多", "文本整体偏强", "中"
    if bearish > bullish + 1:
        return "偏空", "文本整体偏弱", "中"
    return "未知", "", "低"


def _extract_strategy_trend(text: str) -> tuple[str, str, str] | None:
    match = re.search(r"【(?:操作|交易)策略】([^【]+)", text)
    if not match:
        match = re.search(
            r"(?:操作(?:建议|上)|交易策略|策略建议)[：:，,\s]*(?:建议)?\s*([^\n【]{2,160})",
            text,
        )
        if not match:
            return None

    section = match.group(1)
    # 空头/多头思路优先于单纯「观望」
    if re.search(r"空头思路|偏空|看空|逢高(?:做)?空|布局空单|易跌难涨|看空情绪", section):
        return "偏空", section.strip()[:80], "高"
    if re.search(
        r"多头思路|偏多|看多|逢低(?:做)?多|布局多单|易涨难跌|构成支撑|温和反弹|强势震荡",
        section,
    ):
        return "偏多", section.strip()[:80], "高"
    if re.search(r"IH强于IM", section):
        return "偏多", section.strip()[:80], "中"
    if re.search(r"IM强于IH", section):
        return "偏空", section.strip()[:80], "中"

    bearish_hits = len(re.findall(r"总体偏弱|偏弱|承压|偏空|看空|压制|回落|下行|走低", section))
    bullish_hits = len(
        re.findall(r"总体偏强|偏强|反弹|上行|偏多|看多|走高|走强|易涨难跌|支撑", section)
    )
    neutral_hits = len(re.findall(r"震荡|区间|观望|换手|偏稳|短线交易", section))

    if bearish_hits > bullish_hits:
        return "偏空", section.strip()[:80], "高"
    if bullish_hits > bearish_hits:
        return "偏多", section.strip()[:80], "高"
    if neutral_hits:
        return "震荡", section.strip()[:80], "中"
    return None


def _extract_dimension_consensus(text: str) -> tuple[str, str]:
    """大越等「分维度结尾标注中性/偏多/偏空」格式：按票数取共识。"""
    votes: list[str] = []
    for match in DIMENSION_TREND_RE.finditer(text):
        mapped = _map_phrase_to_trend(match.group(1))
        if mapped != "未知":
            votes.append(mapped)

    if len(votes) < 2:
        return "未知", ""

    counts: dict[str, int] = {}
    for vote in votes:
        counts[vote] = counts.get(vote, 0) + 1

    top_trend = max(counts, key=lambda key: (counts[key], abs(TREND_SCORE.get(key, 0))))
    top_count = counts[top_trend]
    summary = f"分维度共识 {top_trend}（{top_count}/{len(votes)} 项）"
    if top_count == len(votes):
        return top_trend, summary
    if top_count >= len(votes) * 0.5:
        return top_trend, summary
    return "震荡", f"分维度分歧（{len(counts)} 种观点），整体震荡"


TREND_SCORE = {
    "看涨": 2,
    "偏多": 1,
    "震荡": 0,
    "中性": 0,
    "偏空": -1,
    "看跌": -2,
}


def _map_phrase_to_trend(phrase: str) -> str:
    mapping = {
        "偏空": ["偏空", "空", "卖出", "沽", "谨慎偏空", "逢高做空"],
        "偏多": ["偏多", "多", "买入", "购", "谨慎偏多", "逢低做多"],
        "看跌": ["看跌", "看空"],
        "看涨": ["看涨", "看多"],
        "震荡": ["震荡", "区间", "观望", "鸡肋", "偏稳", "短线交易"],
        "中性": ["中性"],
    }
    for trend, keywords in mapping.items():
        if any(keyword in phrase for keyword in keywords):
            return trend
    return "未知"


def parse_llm_json(raw: str) -> list[AnalysisResult]:
    data = json.loads(raw)
    items = data if isinstance(data, list) else [data]
    results: list[AnalysisResult] = []
    for item in items:
        commodity = extract_commodity(str(item.get("commodity", "")), "")
        results.append(
            AnalysisResult(
                commodity=commodity,
                trend=str(item.get("trend", "未知")),
                confidence=str(item.get("confidence", "中")),
                source="llm",
                summary=str(item.get("summary", "")),
            )
        )
    return results
