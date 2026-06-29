from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

import config


class Base(DeclarativeBase):
    pass


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(500))
    publish_date: Mapped[str] = mapped_column(String(20), index=True)
    file_path: Mapped[str] = mapped_column(String(500), unique=True)
    file_type: Mapped[str] = mapped_column(String(20))
    raw_content: Mapped[str] = mapped_column(Text, default="")
    cleaned_content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    commodity: Mapped[str] = mapped_column(String(100), index=True)
    trend: Mapped[str] = mapped_column(String(20), index=True)
    confidence: Mapped[str] = mapped_column(String(20), default="中")
    source: Mapped[str] = mapped_column(String(20), default="rule")
    summary: Mapped[str] = mapped_column(Text, default="")
    unknown_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    article: Mapped["Article"] = relationship(back_populates="predictions")


engine = create_engine(config.DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_prediction_columns()


def _ensure_prediction_columns() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "predictions" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("predictions")}
    if "unknown_reason" in columns:
        return
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE predictions ADD COLUMN unknown_reason TEXT DEFAULT ''")
        )
