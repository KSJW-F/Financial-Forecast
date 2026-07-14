from __future__ import annotations

import re

# 期货公司名称，不能作为品种
BROKER_NAMES = {
    "国信", "冠通", "宝城", "五矿", "光大", "大越", "新湖", "国联", "东海", "山金",
    "中衍", "广金", "华龙", "华鑫", "金石", "金信", "浙商", "瑞达", "宏源", "恒泰",
    "迈科", "弘业", "格林大华", "徽商", "长安", "中原", "倍特", "道通", "汇鑫",
    "国元", "中融汇信", "中金财富", "华融融达", "中银", "新湖", "紫金天风", "广金",
    "容请关注格林大华", "本刊由金信", "——国信", "--国信", "--冠通", "----国信",
}

# 标准品种及别名 -> 规范名称
COMMODITY_ALIASES: dict[str, list[str]] = {
    "热卷": ["热卷", "HC"],
    "螺纹": ["螺纹", "螺纹钢", "RB"],
    "铁矿": ["铁矿", "铁矿石", "I"],
    "焦炭": ["焦炭", "J"],
    "焦煤": ["焦煤", "JM"],
    "沪铜": ["沪铜", "铜", "CU"],
    "沪铝": ["沪铝", "铝", "AL"],
    "沪锌": ["沪锌", "锌", "ZN"],
    "沪镍": ["沪镍", "镍", "NI"],
    "沪锡": ["沪锡", "锡", "SN"],
    "沪锡": ["沪锡", "锡", "SN"],
    "原油": ["原油", "SC", "INE原油"],
    "燃油": ["燃油", "燃料油", "FU", "低硫燃料油", "高硫燃料油"],
    "沥青": ["沥青", "BU"],
    "PTA": ["PTA", "TA", "PXTA"],
    "甲醇": ["甲醇", "MA"],
    "聚丙烯": ["聚丙烯", "PP"],
    "聚乙烯": ["聚乙烯", "PE", "L"],
    "乙二醇": ["乙二醇", "EG"],
    "PVC": ["PVC"],
    "LPG": ["LPG", "液化石油气", "PG"],
    "玻璃": ["玻璃", "FG"],
    "纯碱": ["纯碱", "SA"],
    "豆粕": ["豆粕", "M"],
    "菜粕": ["菜粕", "RM"],
    "棕榈油": ["棕榈油", "P"],
    "豆油": ["豆油", "Y"],
    "玉米": ["玉米", "C"],
    "苹果": ["苹果", "AP"],
    "棉花": ["棉花", "CF"],
    "白糖": ["白糖", "SR"],
    "橡胶": ["橡胶", "沪胶", "RU", "20号胶", "NR"],
    "生猪": ["生猪", "LH"],
    "鸡蛋": ["鸡蛋", "JD"],
    "白银": ["白银", "AG"],
    "黄金": ["黄金", "AU"],
    "碳酸锂": ["碳酸锂", "LC"],
    "工业硅": ["工业硅", "SI"],
    "锰硅": ["锰硅", "SM"],
    "硅铁": ["硅铁", "SF"],
    "尿素": ["尿素", "UR"],
    "烧碱": ["烧碱", "SH"],
    "欧线": ["欧线", "EC"],
    "国债": ["国债", "30年国债", "10年国债", "TS", "TF", "T"],
    "沪深300": ["沪深300", "IF"],
    "上证50": ["上证50", "IH"],
    "中证500": ["中证500", "IC"],
    "中证1000": ["中证1000", "IM"],
}

# 板块/主题日报，不是具体可交易品种 —— 不得进入雷达与筛选
SECTOR_LABELS = {
    "股指",
    "股指期权",
    "宏观",
    "农产品",
    "能源化工",
    "有色金属",
    "黑色",
    "油脂",
    "商品",
    "煤焦钢矿",
    "综合",
}

CANONICAL_COMMODITIES = set(COMMODITY_ALIASES.keys())

NOISE_SUBSTRINGS = (
    "请关注", "本刊", "昨日", "今日", "主力合约", "合约", "基差", "指数",
    "免责声明", "日报202", "早评202", "官网", "期货有限", "研究", "报告",
    "宏观", "黑色", "能源化工", "有色", "农产品日报",
)

INVALID_COMMODITY_RE = re.compile(
    r"^[\d\.、；;：:%\-—_]+|^[\d]+[\.、；;]|合约$|主力$|^\d+$|^[A-Za-z]{1,2}$"
)


def is_valid_commodity(name: str) -> bool:
    if not name:
        return False
    if name in SECTOR_LABELS:
        return False
    if name in BROKER_NAMES:
        return False
    if any(noise in name for noise in NOISE_SUBSTRINGS):
        return False
    if INVALID_COMMODITY_RE.search(name):
        return False
    if len(name) > 12:
        return False
    return name in CANONICAL_COMMODITIES


def _match_alias(text: str) -> str | None:
    best: tuple[int, str] | None = None
    for canonical, aliases in COMMODITY_ALIASES.items():
        for alias in aliases:
            if alias.upper() == text.upper() or alias == text:
                return canonical
            if len(alias) >= 2 and alias in text:
                score = len(alias)
                if best is None or score > best[0]:
                    best = (score, canonical)
    return best[1] if best else None


def normalize_commodity(raw: str) -> str | None:
    if not raw:
        return None

    text = raw.strip()
    text = re.sub(r"^[\s\-—_]+", "", text)
    text = re.sub(r"[\s\-—_]+$", "", text)
    text = text.replace("期货", "").strip()

    if not text or text in BROKER_NAMES:
        return None

    if text in SECTOR_LABELS:
        return None

    matched = _match_alias(text)
    if matched and matched not in SECTOR_LABELS and is_valid_commodity(matched):
        return matched

    if is_valid_commodity(text):
        return text
    return None


def extract_commodity(title: str, content: str = "") -> str:
    title = title.strip()
    candidates: list[str] = []

    bracket = re.search(r"【([^【】]{1,12})?(?:日报|早评|晚评|周报)】?", title)
    if bracket:
        candidates.append(bracket.group(1) or "")

    after_futures = re.search(r"期货([\u4e00-\u9fffA-Z]{1,8})(?:早评|日评|周报|月报)", title)
    if after_futures:
        candidates.append(after_futures.group(1))

    before_review = re.search(r"([\u4e00-\u9fffA-Z]{1,8})(?:早评|日评|周报)", title)
    if before_review:
        candidates.append(before_review.group(1))

    # 股指类：落到具体指数品种，不归入「股指」板块
    if "沪深300" in title or re.search(r"\bIF\b", title):
        candidates.append("沪深300")
    if "上证50" in title or re.search(r"\bIH\b", title):
        candidates.append("上证50")
    if "中证500" in title or re.search(r"\bIC\b", title):
        candidates.append("中证500")
    if "中证1000" in title or re.search(r"\bIM\b", title):
        candidates.append("中证1000")

    daily_tips = re.search(r"每日提示[—\-–]+([^（(\n]+)", title)
    if daily_tips:
        candidates.append(daily_tips.group(1).strip())

    colon_title = re.search(r"^([^：:\s]{1,8})[：:]", title)
    if colon_title:
        candidates.append(colon_title.group(1).strip())

    for candidate in candidates:
        normalized = normalize_commodity(candidate)
        if normalized:
            return normalized

    for source in (title, content[:300]):
        matched = _match_alias(source)
        if matched and is_valid_commodity(matched):
            return matched

    return "综合"
