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
        if fallback and not is_garbled_text(fallback):
            content = fallback

    if is_garbled_text(content):
        ocr_content = _extract_with_ocr(file_path)
        if ocr_content and not is_garbled_text(ocr_content):
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
    return False


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
    for line in lines[:8]:
        if 4 <= len(line) <= 80 and any(key in line for key in ("期货", "早评", "日评", "早报", "文字", "策略")):
            return line

    stem = file_path.stem
    parts = stem.split("_")
    return f"{parts[0]}研报" if parts else stem
