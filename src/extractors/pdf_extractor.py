from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber

logger = logging.getLogger(__name__)
logging.getLogger("pdfminer").setLevel(logging.ERROR)


def extract_pdf(file_path: Path) -> tuple[str, str]:
    content = _extract_with_pdfplumber(file_path)
    if is_garbled_text(content):
        fallback = _extract_with_pymupdf(file_path)
        if fallback and (
            not is_garbled_text(fallback)
            or _text_quality(fallback) > _text_quality(content)
        ):
            content = fallback

    # 乱码 / 符号噪声假正文 → OCR；图表型仅在正文质量差时 OCR（避免数据 PDF 白跑 OCR）
    should_ocr = is_garbled_text(content) or _needs_ocr_fallback(content)
    if not should_ocr and is_chart_heavy_pdf(content) and _text_quality(content) < 0.12:
        should_ocr = True
    # 策略封面页：正文只有分析师介绍，真实策略在图里
    if (
        not should_ocr
        and re.search(r"每周核心策略", content[:400] or "")
        and _viewpoint_signal(content) == 0
        and len(content) < 1200
    ):
        should_ocr = True
    if should_ocr:
        ocr_content = _extract_with_ocr(file_path)
        if ocr_content:
            if (
                is_garbled_text(content)
                or _text_quality(ocr_content) > _text_quality(content)
                or _viewpoint_signal(ocr_content) > _viewpoint_signal(content)
            ):
                content = ocr_content

    title = _extract_title(content, file_path)
    return title, content


def is_garbled_text(text: str) -> bool:
    if not text or len(text) < 80:
        return True
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    ratio = chinese / len(text)
    if ratio < 0.08:
        return True
    if text.count("(cid:") > 5:
        return True
    # 冠通等：PyMuPDF 抽出大量 !"#$ 噪声，中文刚好卡在 8% 阈值上
    if _symbol_noise_ratio(text) > 0.05 and _viewpoint_signal(text) == 0:
        return True
    if ratio < 0.12 and _viewpoint_signal(text) == 0 and _symbol_noise_ratio(text) > 0.03:
        return True
    return False


def is_chart_heavy_pdf(text: str) -> bool:
    """图表型早报：大量「图N：」/数据来源，几乎没有操作/观点句。"""
    if not text or len(text) < 200:
        return True
    chart_hits = len(re.findall(r"图\s*\d+\s*[：:]", text))
    source_hits = text.count("数据来源") + text.count("WIND") + text.count("MYSTEEL")
    viewpoint = _viewpoint_signal(text)
    data_title = bool(
        re.search(r"数据跟踪|库存周度|产业链追踪|盘面套利|基差及资金", text[:400])
    )
    # 纯图表页：有图/数据源，但几乎没有可操作观点
    if chart_hits >= 3 and viewpoint == 0:
        return True
    if source_hits >= 3 and viewpoint == 0 and chart_hits >= 1:
        return True
    # 即使 OCR 后仍只有报价表/图注，无操作句
    if viewpoint == 0 and (chart_hits + source_hits) >= 4 and len(text) > 800:
        return True
    if data_title and viewpoint == 0 and source_hits >= 2:
        return True
    return False


def _viewpoint_signal(text: str) -> int:
    return len(
        re.findall(
            r"操作建议|操作上|核心观点|短线(?:偏多|偏空|空头|多头)|"
            r"偏多|偏空|看涨|看跌|震荡(?:为主|运行)|中性看待|"
            r"【操作策略】|【交易策略】|布局多单|布局空单|偏稳波动",
            text,
        )
    )


def _symbol_noise_ratio(text: str) -> float:
    if not text:
        return 1.0
    noisy = sum(1 for char in text if char in "!@#$%^&*()[]{}|\\<>")
    return noisy / len(text)


def _text_quality(text: str) -> float:
    if not text:
        return 0.0
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return chinese / len(text) - _symbol_noise_ratio(text)


def _needs_ocr_fallback(text: str) -> bool:
    """假正文：有一定中文比例但几乎全是噪声/无观点。"""
    if not text or len(text) < 200:
        return False
    return (
        _viewpoint_signal(text) == 0
        and _symbol_noise_ratio(text) > 0.04
        and _text_quality(text) < 0.1
    )


def _extract_with_pdfplumber(file_path: Path) -> str:
    texts: list[str] = []
    try:
        with pdfplumber.open(str(file_path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    texts.append(page_text.strip())
    except Exception as exc:
        logger.debug("pdfplumber failed for %s: %s", file_path, exc)
    return "\n".join(texts)


def _extract_with_pymupdf(file_path: Path) -> str:
    try:
        import fitz
    except ImportError:
        return ""

    texts: list[str] = []
    try:
        with fitz.open(str(file_path)) as doc:
            for page in doc:
                page_text = page.get_text() or ""
                if page_text.strip():
                    texts.append(page_text.strip())
    except Exception as exc:
        logger.debug("pymupdf failed for %s: %s", file_path, exc)
    return "\n".join(texts)


def _extract_with_ocr(file_path: Path) -> str:
    try:
        from src.extractors.ocr import ocr_pdf_pages
    except ImportError:
        return ""

    content = ocr_pdf_pages(file_path)
    if content:
        logger.info("PDF OCR extracted %d chars from %s", len(content), file_path.name)
    return content


def _extract_title(content: str, file_path: Path) -> str:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    keywords = (
        "期货", "早评", "日评", "早报", "文字", "策略", "周报", "日报", "日刊",
        "数据跟踪", "产业链", "早盘提示", "资讯", "豆粕", "油脂", "生猪", "原油",
    )
    for line in lines[:20]:
        candidate = _normalize_title_line(line)
        if not candidate or _is_bad_title_line(candidate):
            continue
        if 4 <= len(candidate) <= 80 and (
            any(key in candidate for key in keywords)
            or "：" in candidate
            or ":" in candidate
        ):
            return candidate

    # 首行若是「品种：观点」类也可用
    for line in lines[:5]:
        candidate = _normalize_title_line(line)
        if candidate and not _is_bad_title_line(candidate) and 6 <= len(candidate) <= 60:
            return candidate

    stem = file_path.stem
    parts = stem.split("_")
    return f"{parts[0]}研报" if parts else stem


def _normalize_title_line(line: str) -> str:
    # 「金 信 期 货 日 刊」→「金信期货日刊」
    if re.fullmatch(r"(?:[\u4e00-\u9fff]\s+){2,}[\u4e00-\u9fff]", line.strip()):
        return re.sub(r"\s+", "", line)
    return re.sub(r"\s{2,}", " ", line).strip()


def _is_bad_title_line(line: str) -> bool:
    if re.search(r"[\w.+-]+@[\w.-]+\.\w+", line):
        return True
    bad_markers = (
        "从业资格", "投资咨询从业", "证书号", "免责", "本刊由",
        "联系电话", "联系方式", "研究员：", "分析师：", "数据来源",
        "包图网", "ibaotu", "GOLDTRUST",
    )
    if any(marker in line for marker in bad_markers):
        return True
    if "投资有风险" in line:
        return True
    return False
