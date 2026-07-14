from __future__ import annotations

import re
from pathlib import Path

from src.extractors.pdf_extractor import is_chart_heavy_pdf

COMMODITY_HINTS = (
    "螺纹", "热卷", "铁矿", "焦炭", "焦煤", "锰硅", "硅铁", "纯碱", "玻璃",
    "沪铜", "沪铝", "沪锌", "沪镍", "沪锡", "原油", "燃油", "沥青", "PTA",
    "甲醇", "豆粕", "菜粕", "棕榈油", "豆油", "玉米", "苹果", "棉花", "白糖", "橡胶",
    "生猪", "鸡蛋", "白银", "黄金", "工业硅", "烧碱", "聚乙烯", "聚丙烯", "PVC",
)


def needs_chart_ai(title: str, content: str, file_type: str = "") -> bool:
    """规则无法识别、且内容像图表型早报时，走图表 AI 通道。"""
    if not content or len(content.strip()) < 80:
        return False
    if file_type == "pdf" and is_chart_heavy_pdf(content):
        return True
    chart_hits = len(re.findall(r"图\s*\d+\s*[：:]", content))
    source_hits = content.count("数据来源") + content.count("WIND") + content.count("MYSTEEL")
    if chart_hits >= 3 and source_hits >= 2:
        return True
    if "早报" in title and chart_hits >= 2:
        return True
    # OCR 价表/产业日报：大量指标行，少明确观点句
    table_hits = sum(
        1
        for marker in ("收盘价", "持仓量", "成交量", "较昨日", "库存", "基差", "价差")
        if marker in content
    )
    if table_hits >= 3 and len(content) > 400:
        return True
    if any(k in title for k in ("日评", "早评", "产业日报", "早盘提示")) and table_hits >= 2:
        return True
    if "早盘提示" in title or "多（空）" in content or "多(空)" in content:
        return True
    blob = f"{title}\n{content[:800]}"
    if re.search(
        r"数据跟踪|库存周度|产业链追踪|盘面套利|早盘速递|资讯早间|"
        r"每周核心策略|全球经济展望|基差及资金|沉淀资金",
        blob,
    ):
        return True
    return False


def build_chart_context(title: str, content: str, max_chars: int = 3500) -> str:
    """把图表型正文压缩成 AI 可读的结构化摘要。"""
    commodities = [name for name in COMMODITY_HINTS if name in title or name in content[:2000]]
    deltas = _extract_price_moves(content)
    sections = _extract_section_snippets(content)

    parts = [
        f"标题：{title.strip()}",
        f"识别到的品种：{('、'.join(commodities[:12]) if commodities else '未明确')}",
    ]
    if deltas:
        parts.append("价格/涨跌线索：")
        parts.extend(f"- {item}" for item in deltas[:25])
    if sections:
        parts.append("正文片段：")
        parts.extend(sections[:12])
    else:
        compact = re.sub(r"\n{3,}", "\n\n", content.strip())
        parts.append("OCR/文本摘录：")
        parts.append(compact[: max_chars - 400])

    text = "\n".join(parts)
    return text[:max_chars]


def _extract_price_moves(content: str) -> list[str]:
    moves: list[str] = []
    # 名称 + 数字 + 涨跌幅/变动
    for match in re.finditer(
        r"([\u4e00-\u9fffA-Za-z0-9#％%]{2,16})\s*[：:]?\s*(-?\d+(?:\.\d+)?)\s*[，,]?\s*([+\-－]\d+(?:\.\d+)?%?)",
        content,
    ):
        name, value, change = match.group(1), match.group(2), match.group(3)
        if any(noise in name for noise in ("数据来源", "合约", "图", "页")):
            continue
        moves.append(f"{name} 现价/指标 {value}，变动 {change}")

    # 单独的涨跌描述
    for match in re.finditer(
        r"([\u4e00-\u9fff]{2,8}).{0,8}(上涨|下跌|走强|走弱|偏强|偏弱|反弹|回落|承压|震荡)[^\n。]{0,20}",
        content,
    ):
        moves.append(match.group(0).strip())

    # 去重保序
    seen: set[str] = set()
    unique: list[str] = []
    for item in moves:
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def _extract_section_snippets(content: str) -> list[str]:
    snippets: list[str] = []
    for name in COMMODITY_HINTS:
        for match in re.finditer(rf"{re.escape(name)}[^\n]{{0,120}}", content):
            text = match.group(0).strip()
            if len(text) >= 6:
                snippets.append(text)
            if len(snippets) >= 20:
                return snippets
    return snippets


def render_pdf_page_previews(file_path: Path, max_pages: int = 2, zoom: float = 1.6) -> list[Path]:
    """可选：把 PDF 首页渲染成图片，供 OpenAI Vision 使用。"""
    try:
        import fitz
    except ImportError:
        return []

    out_dir = file_path.parent / "_pdf_preview"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    try:
        with fitz.open(str(file_path)) as doc:
            for index in range(min(max_pages, doc.page_count)):
                page = doc[index]
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                target = out_dir / f"{file_path.stem}_p{index + 1}.png"
                pix.save(str(target))
                paths.append(target)
    except Exception:
        return []
    return paths


def heuristic_chart_trends(title: str, content: str) -> list:
    """无 AI 时：根据价表涨跌符号做弱启发式趋势判断。"""
    from src.analyzer.commodity import extract_commodity, normalize_commodity
    from src.analyzer.rule_analyzer import AnalysisResult

    text = f"{title}\n{content}"

    # 格林大华早盘提示：板块/品种/多（空）
    table_results = _parse_long_short_table(title, content)
    if table_results:
        return table_results

    # 策略句优先于价表涨跌符号
    if re.search(r"布局多单|易涨难跌|逢(?:价格)?回落.{0,8}多单|多头思路|构成利好", text):
        trend, summary, confidence = "偏多", "交易策略偏多（启发式）", "中"
    elif re.search(r"布局空单|易跌难涨|逢(?:价格)?反弹.{0,8}空单|空头思路|构成利空|引起恐慌", text):
        trend, summary, confidence = "偏空", "交易策略偏空（启发式）", "中"
    elif re.search(r"全球经济向上|经济向上的趋势并未", text):
        trend, summary, confidence = "偏多", "宏观展望偏多（启发式）", "中"
    elif re.search(
        r"数据跟踪|库存周度|产业链追踪|盘面套利|基差及资金|沉淀资金",
        f"{title}\n{content[:500]}",
    ):
        # 纯数据跟踪：无观点句时按震荡占位，避免长期未知
        trend, summary, confidence = "震荡", "数据跟踪型报告（启发式中性）", "低"
    elif re.search(r"早盘速递|资讯早间|热点资讯", f"{title}\n{content[:300]}"):
        trend, summary, confidence = "震荡", "资讯速递无明确品种观点（启发式）", "低"
    elif re.search(r"每周核心策略", f"{title}\n{content[:300]}"):
        trend, summary, confidence = "震荡", "策略封面页无正文观点（启发式）", "低"
    else:
        up = len(re.findall(r"[+\＋]\d+(?:\.\d+)?%?", text))
        down = len(re.findall(r"[-\－]\d+(?:\.\d+)?%?", text))
        weak = len(re.findall(r"偏弱|走弱|回落|下跌|承压", text))
        strong = len(re.findall(r"偏强|走强|反弹|上涨", text))
        score = (up - down) + (strong - weak) * 2
        if score <= -3:
            trend, summary, confidence = "偏空", "价表整体偏弱（启发式）", "低"
        elif score >= 3:
            trend, summary, confidence = "偏多", "价表整体偏强（启发式）", "低"
        else:
            trend, summary, confidence = "震荡", "图表早报方向不明，按震荡（启发式）", "低"

    commodities: list[str] = []
    for name in COMMODITY_HINTS:
        if name in title or name in content[:2500]:
            mapped = normalize_commodity(name) or name
            if mapped not in commodities:
                commodities.append(mapped)

    if not commodities:
        commodities = [extract_commodity(title, content)]

    # 黑色金属早报：最多返回前 6 个品种，避免噪声
    results = []
    for commodity in commodities[:6]:
        results.append(
            AnalysisResult(
                commodity=commodity,
                trend=trend,
                confidence=confidence,
                source="chart-heuristic",
                summary=summary,
            )
        )
    return results


def _parse_long_short_table(title: str, content: str) -> list:
    """解析「品种 多/空」类早盘提示表（含格林大华排版错乱时的宏观多空）。"""
    from src.analyzer.commodity import normalize_commodity
    from src.analyzer.rule_analyzer import AnalysisResult

    is_tip = (
        "早盘提示" in title
        or "多（空）" in content
        or "多(空)" in content
        or "Morning session" in content
    )
    if not is_tip and not re.search(r"[（(](?:多|空|偏多|偏空)[）)]", content[:1200]):
        return []

    results = []
    seen: set[str] = set()

    # 格林排版错乱：宏观与 全球 / （多）
    if re.search(r"宏观.{0,12}[（(]多[）)]|[（(]多[）)].{0,20}宏观", content[:1500]):
        results.append(
            AnalysisResult(
                commodity="宏观",
                trend="偏多",
                confidence="中",
                source="chart-heuristic",
                summary="早盘提示：宏观多",
            )
        )
        seen.add("宏观")
    elif re.search(r"宏观.{0,12}[（(]空[）)]|[（(]空[）)].{0,20}宏观", content[:1500]):
        results.append(
            AnalysisResult(
                commodity="宏观",
                trend="偏空",
                confidence="中",
                source="chart-heuristic",
                summary="早盘提示：宏观空",
            )
        )
        seen.add("宏观")

    for match in re.finditer(
        r"([\u4e00-\u9fffA-Za-z#]{2,10})\s*[（(]?(多|空|偏多|偏空)[）)]?",
        content,
    ):
        name, side = match.group(1), match.group(2)
        if name in {"板块", "品种", "宏观", "金融", "全球", "经济", "重要资讯", "宏观与"}:
            continue
        commodity = normalize_commodity(name) or name
        if commodity in seen or commodity == "综合":
            continue
        if commodity not in COMMODITY_HINTS and name not in COMMODITY_HINTS:
            continue
        seen.add(commodity)
        trend = "偏多" if side in {"多", "偏多"} else "偏空"
        results.append(
            AnalysisResult(
                commodity=commodity,
                trend=trend,
                confidence="中",
                source="chart-heuristic",
                summary=f"早盘提示：{name}{side}",
            )
        )
        if len(results) >= 8:
            break
    return results
