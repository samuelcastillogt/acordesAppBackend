from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    works: Mapped[list[WorkArtist]] = relationship(back_populates="artist")


class Work(Base):
    __tablename__ = "works"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    normalized_title: Mapped[str] = mapped_column(String(300), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    artists: Mapped[list[WorkArtist]] = relationship(back_populates="work")
    source_records: Mapped[list[SourceRecord]] = relationship(back_populates="work")


class WorkArtist(Base):
    __tablename__ = "work_artists"

    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(40), default="performer")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)

    work: Mapped[Work] = relationship(back_populates="artists")
    artist: Mapped[Artist] = relationship(back_populates="works")


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("provider", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey("works.id"), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    external_id: Mapped[str] = mapped_column(String(120))
    source_url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text)
    source_artist_slug: Mapped[str] = mapped_column(String(120), index=True)
    source_title: Mapped[str] = mapped_column(String(300))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_file: Mapped[str] = mapped_column(Text)
    rights_status: Mapped[str] = mapped_column(String(20), default="unknown", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    work: Mapped[Work] = relationship(back_populates="source_records")
    quality_flags: Mapped[list[QualityFlag]] = relationship(
        back_populates="source_record", cascade="all, delete-orphan"
    )


class QualityFlag(Base):
    __tablename__ = "quality_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_record_id: Mapped[int] = mapped_column(ForeignKey("source_records.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(20))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source_record: Mapped[SourceRecord] = relationship(back_populates="quality_flags")


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    importer_version: Mapped[str] = mapped_column(String(30))
    manifest_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    discovered: Mapped[int] = mapped_column(default=0)
    imported: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ContentReport(Base):
    __tablename__ = "content_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_slug: Mapped[str] = mapped_column(String(180))
    reason: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
