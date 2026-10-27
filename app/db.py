"""SQLAlchemy persistence for image metadata."""

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import BigInteger, DateTime, String, create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


DATABASE_URL = os.getenv("DATABASE_URL", "")


class Base(DeclarativeBase):
    pass


class ImageMetadataRecord(Base):
    __tablename__ = "image_metadata"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    extension: Mapped[str] = mapped_column(String(32), nullable=False)
    last_modified: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None


def initialize_database() -> None:
    """Create the lab table when a database URL is configured."""
    if engine is not None:
        Base.metadata.create_all(engine)


def database_enabled() -> bool:
    return engine is not None


@contextmanager
def session_scope() -> Iterator[Session]:
    if engine is None:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def save_metadata(name: str, size: int, extension: str, last_modified: datetime) -> None:
    if engine is None:
        return
    try:
        with session_scope() as session:
            record = session.scalar(
                select(ImageMetadataRecord).where(ImageMetadataRecord.name == name)
            )
            if record is None:
                record = ImageMetadataRecord(name=name)
                session.add(record)
            record.size_bytes = size
            record.extension = extension
            record.last_modified = last_modified
    except SQLAlchemyError as exc:
        raise RuntimeError("Could not save image metadata to the database") from exc


def delete_metadata(name: str) -> None:
    if engine is None:
        return
    try:
        with session_scope() as session:
            record = session.scalar(
                select(ImageMetadataRecord).where(ImageMetadataRecord.name == name)
            )
            if record is not None:
                session.delete(record)
    except SQLAlchemyError as exc:
        raise RuntimeError("Could not delete image metadata from the database") from exc
