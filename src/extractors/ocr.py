from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ocr_engine: Any | None = None
TALL_IMAGE_HEIGHT = 3500
CHUNK_HEIGHT = 2500
CHUNK_OVERLAP = 200


def _get_engine() -> Any | None:
    global _ocr_engine
    if _ocr_engine is not None:
        return _ocr_engine
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
        return _ocr_engine
    except ImportError:
        logger.debug("rapidocr-onnxruntime not installed")
        return None


def ocr_image(image: Any) -> str:
    engine = _get_engine()
    if engine is None:
        return _filter_ocr_text(_ocr_with_pytesseract(image))

    try:
        result, _ = engine(image)
        if not result:
            return ""
        text = "\n".join(line[1] for line in result if len(line) > 1 and line[1]).strip()
        return _filter_ocr_text(text)
    except Exception as exc:
        logger.debug("RapidOCR failed: %s", exc)
        return _filter_ocr_text(_ocr_with_pytesseract(image))


def ocr_file(file_path: Path) -> str:
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        with Image.open(file_path) as image:
            prepared = _prepare_image(image)
            if prepared.height > TALL_IMAGE_HEIGHT:
                text = _ocr_tall_image(prepared)
            else:
                text = ocr_image(prepared)
            if _is_garbled_ocr(text) and prepared.height > 1200:
                text = _ocr_tall_image(prepared)
            return _filter_ocr_text(text)
    except Exception as exc:
        logger.debug("OCR file failed for %s: %s", file_path, exc)
        return ""


def ocr_pdf_pages(file_path: Path, max_pages: int | None = None, zoom: float = 2.0) -> str:
    try:
        import fitz
        from PIL import Image

        import config
    except ImportError:
        return ""

    if max_pages is None:
        max_pages = config.PDF_OCR_MAX_PAGES

    texts: list[str] = []
    try:
        with fitz.open(str(file_path)) as doc:
            for page in doc[:max_pages]:
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                page_text = ocr_image(image)
                if page_text.strip():
                    texts.append(page_text.strip())
    except Exception as exc:
        logger.debug("PDF OCR failed for %s: %s", file_path, exc)
    return _filter_ocr_text("\n".join(texts))


def _prepare_image(image: Any) -> Any:
    from PIL import Image

    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        return background
    return image.convert("RGB")


def _ocr_tall_image(image: Any) -> str:
    import config

    texts: list[str] = []
    y = 0
    height = image.height
    chunk_count = 0
    max_chunks = max(config.OCR_TALL_MAX_CHUNKS, 1)
    while y < height and chunk_count < max_chunks:
        bottom = min(y + CHUNK_HEIGHT, height)
        crop = image.crop((0, y, image.width, bottom))
        chunk_text = ocr_image(crop)
        if chunk_text.strip():
            texts.append(chunk_text.strip())
        chunk_count += 1
        if bottom >= height:
            break
        y += CHUNK_HEIGHT - CHUNK_OVERLAP
    return _merge_ocr_chunks(texts)


def _merge_ocr_chunks(chunks: list[str]) -> str:
    if not chunks:
        return ""
    merged_lines: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        for line in chunk.splitlines():
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            merged_lines.append(line)
    return "\n".join(merged_lines)


def _filter_ocr_text(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _is_noise_ocr_line(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _is_noise_ocr_line(line: str) -> bool:
    if len(line) <= 2:
        return True
    chinese = sum(1 for char in line if "\u4e00" <= char <= "\u9fff")
    ratio = chinese / len(line)
    if len(line) >= 8 and ratio < 0.08:
        return True
    if len(line) >= 4 and ratio == 0 and not any(char.isdigit() for char in line):
        return True
    return False


def _is_garbled_ocr(text: str) -> bool:
    if not text or len(text) < 80:
        return True
    chinese = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return chinese / len(text) < 0.12


def _ocr_with_pytesseract(image: Any) -> str:
    try:
        import pytesseract
    except ImportError:
        return ""

    try:
        return pytesseract.image_to_string(image, lang="chi_sim").strip()
    except Exception as exc:
        logger.debug("pytesseract failed: %s", exc)
        return ""
