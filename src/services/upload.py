"""用户上传研报：落盘到 data/uploads/，再走统一提取+分析入库。"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

import config
from src.models import Prediction, SessionLocal, init_db
from src.pipeline import process_file

ALLOWED_EXTENSIONS = {".html", ".htm", ".pdf", ".png", ".jpg", ".jpeg", ".PDF", ".PNG"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@dataclass
class UploadResult:
    ok: bool
    message: str
    article_id: int | None = None
    title: str = ""
    broker: str = ""
    predictions: list[dict] | None = None
    file_path: str = ""


def _safe_stem(name: str) -> str:
    stem = Path(name).stem
    stem = secure_filename(stem) or "upload"
    stem = re.sub(r"[^\w\u4e00-\u9fff\-]+", "_", stem).strip("_")
    return (stem or "upload")[:80]


def _normalize_broker(broker: str, filename: str) -> str:
    text = (broker or "").strip()
    if text:
        return text[:40]
    stem = Path(filename).stem
    # 兼容「机构_编号」命名
    if "_" in stem:
        return stem.split("_")[0][:40]
    return "用户上传"


def save_and_process_upload(
    file: FileStorage,
    *,
    broker: str = "",
    publish_date: str = "",
    use_llm: bool | None = None,
) -> UploadResult:
    if not file or not file.filename:
        return UploadResult(False, "未选择文件")

    original = file.filename
    suffix = Path(original).suffix
    if suffix.lower() not in {ext.lower() for ext in ALLOWED_EXTENSIONS}:
        return UploadResult(False, f"不支持的文件类型：{suffix or '无扩展名'}（支持 HTML/PDF/PNG/JPG）")

    data = file.read()
    if not data:
        return UploadResult(False, "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        return UploadResult(False, "文件超过 25MB 限制")

    today = datetime.now().strftime("%Y%m%d")
    date_folder = publish_date.replace("-", "").strip() if publish_date else today
    if not (len(date_folder) == 8 and date_folder.isdigit()):
        date_folder = today

    broker_name = _normalize_broker(broker, original)
    unique = uuid.uuid4().hex[:8]
    filename = f"{broker_name}_{_safe_stem(original)}_{unique}{suffix}"
    # Windows 友好：去掉路径非法字符
    filename = re.sub(r'[<>:"/\\|?*]', "_", filename)

    target_dir = config.DATA_DIR / "uploads" / date_folder
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / filename
    target_path.write_bytes(data)

    init_db()
    session = SessionLocal()
    try:
        article = process_file(target_path, session, reprocess=False)
        if not article:
            return UploadResult(False, "处理失败：未能生成文章记录", file_path=str(target_path))

        # 允许用户覆盖机构名/日期
        if broker_name and article.broker != broker_name:
            article.broker = broker_name
        if date_folder and article.publish_date in {"unknown", ""}:
            article.publish_date = date_folder

        session.commit()
        session.refresh(article)

        preds = (
            session.query(Prediction)
            .filter_by(article_id=article.id)
            .order_by(Prediction.id.asc())
            .all()
        )
        return UploadResult(
            ok=True,
            message="上传并分析完成",
            article_id=article.id,
            title=article.title,
            broker=article.broker,
            file_path=article.file_path,
            predictions=[
                {
                    "commodity": p.commodity,
                    "trend": p.trend,
                    "confidence": p.confidence,
                    "source": p.source,
                    "summary": p.summary,
                }
                for p in preds
            ],
        )
    except Exception as exc:
        session.rollback()
        return UploadResult(False, f"处理失败：{exc}", file_path=str(target_path))
    finally:
        session.close()
