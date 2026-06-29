from pathlib import Path

from src.cleaner import clean_text
from src.analyzer.commodity import extract_commodity
from src.analyzer.rule_analyzer import analyze_with_rules
from src.extractors.html_extractor import extract_html


def test_clean_text_removes_disclaimer():
    raw = "热卷：震荡运行\n重要免责声明\n本报告不构成投资建议"
    cleaned = clean_text(raw)
    assert "免责声明" not in cleaned
    assert "热卷" in cleaned


def test_rule_analyzer_extracts_explicit_trend():
    title = "国信期货热卷早评20250401"
    content = "热卷：建材拖累 市场抢跑\n操作建议:短线偏空参与。"
    results = analyze_with_rules(title, content)
    assert len(results) == 1
    assert results[0].trend == "偏空"
    assert results[0].source == "rule"


def test_html_extractor_reads_sample_file():
    sample = Path("data/20250401/国信期货_323528_0.html")
    if not sample.exists():
        return
    title, content = extract_html(sample)
    assert "热卷" in title or "国信" in title
    assert "操作建议" in content or "偏空" in content


def test_commodity_extraction_from_title():
    assert extract_commodity("国信期货热卷早评20250401", "") == "热卷"
    assert extract_commodity("【MA日报20250429】", "") == "甲醇"
    assert extract_commodity("股票及股指期权早盘建议——20250429", "") == "股指期权"
    assert extract_commodity("浙商期货官网", "冠通期货早评") == "综合"
