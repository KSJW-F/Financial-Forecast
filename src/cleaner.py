from __future__ import annotations

import re

DISCLAIMER_PATTERNS = [
    r"重要免责声明[\s\S]*?最终解释权[。\.]?",
    r"免责申明[:：][\s\S]*?(?:不得对本报告进行有悖原意|最终解释权)[。\.]?",
    r"本报告版权归[\s\S]*?(?:不得对本报告进行有悖原意|最终解释权)[。\.]?",
    r"Copyright[\s\S]{0,300}版权所有",
]

NOISE_KEYWORDS = (
    "返回顶部",
    "友情链接",
    "网站地图",
    "沪ICP备",
    "沪公网安备",
    "在线客服",
    "营业网点",
    "无障碍浏览",
    "公司简介",
    "股东信息",
    "组织架构",
    "社会招聘",
    "校园招聘",
    "网上开户",
    "下载专区",
    "投资者园地",
    "收藏本页面",
    "相关信息",
    "客服电话",
    "版权所有",
    "分析师：",
    "从业资格号",
    "投资咨询号",
    "交易咨询业务资格",
    "证监许可",
)

ANALYST_FOOTER_PATTERNS = [
    r"国信期货交易咨询业务资格：[\s\S]*?(?:@\w+\.\w+|\.com\.cn)\s*",
    r"分析师：[\s\S]*?(?:@\w+\.\w+|\.com\.cn)\s*",
]


def clean_text(text: str) -> str:
    cleaned = text.replace("\u3000", " ").replace("&emsp;", " ")
    for pattern in DISCLAIMER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    for pattern in ANALYST_FOOTER_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

    lines = []
    for line in cleaned.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if _is_noise_line(line):
            continue
        lines.append(line)

    return "\n".join(lines).strip()


def _is_noise_line(line: str) -> bool:
    if any(keyword in line for keyword in NOISE_KEYWORDS):
        return True
    if re.fullmatch(r"\[\w+\]", line):
        return True
    if re.fullmatch(r"电话：\d[\d-]{6,}", line):
        return True
    if re.fullmatch(r"邮箱：\S+", line):
        return True
    if len(line) <= 2:
        return True
    return False

