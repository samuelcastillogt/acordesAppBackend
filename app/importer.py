from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db_models import Artist, ImportRun, QualityFlag, SourceRecord, Work, WorkArtist
from .models import ImportSummary
from .repository import normalize_search


IMPORTER_VERSION = "1.0.0"
PROVIDER = "acordesweb"
ALLOWED_HOST = "acordesweb.com"
ARTIST_NAMES = {
    "soda-stereo": "Soda Stereo",
    "gustavo-cerati": "Gustavo Cerati",
}
SHA256_PATTERN = r"^[0-9a-f]{64}$"


class ImportDataError(RuntimeError):
    """Raised when the private snapshot fails validation."""


class ManifestSong(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artist_slug: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1, max_length=300)
    url: str
    canonical_url: str
    external_id: str = Field(min_length=1, max_length=120)
    notation: str
    captured_at: datetime
    content_hash: str = Field(pattern=SHA256_PATTERN)
    file: str

    @model_validator(mode="after")
    def validate_source(self) -> "ManifestSong":
        if self.artist_slug not in ARTIST_NAMES:
            raise ValueError("artist is outside the import allowlist")
        for value in (self.url, self.canonical_url):
            parsed = urlparse(value)
            if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
                raise ValueError("source URL is outside the provider allowlist")
        if self.url != self.canonical_url:
            raise ValueError("source and canonical URL must match")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    source: str
    artists: list[str]
    discovered: int = Field(ge=0)
    saved: int = Field(ge=0)
    failed: int = Field(ge=0)
    songs: list[ManifestSong]
    failures: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_counts(self) -> "Manifest":
        if self.source != "https://acordesweb.com":
            raise ValueError("unexpected manifest source")
        if self.saved != len(self.songs):
            raise ValueError("saved count does not match songs")
        if self.discovered != self.saved + self.failed:
            raise ValueError("discovered count does not match saved + failed")
        if self.failed != len(self.failures):
            raise ValueError("failed count does not match failures")
        if set(self.artists) != {song.artist_slug for song in self.songs}:
            raise ValueError("artist inventory does not match songs")
        return self


@dataclass(frozen=True)
class ParsedTextFile:
    body_hash: str
    flags: tuple[tuple[str, str, str | None], ...]


def import_snapshot(session: Session, data_dir: Path) -> ImportSummary:
    manifest_path = data_dir / "manifest.json"
    if data_dir.is_symlink() or manifest_path.is_symlink():
        raise ImportDataError("manifest_symlink_rejected")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as error:
        raise ImportDataError("manifest_unavailable") from error

    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    run = ImportRun(
        id=str(uuid.uuid4()),
        importer_version=IMPORTER_VERSION,
        manifest_hash=manifest_hash,
        status="running",
    )
    session.add(run)
    session.commit()

    try:
        raw_manifest = json.loads(manifest_bytes.decode("utf-8", errors="strict"))
        manifest = Manifest.model_validate(raw_manifest)
        _validate_uniqueness(manifest)
        run.discovered = manifest.discovered

        imported = 0
        flag_count = 0
        records_by_hash: dict[str, list[SourceRecord]] = {}
        for entry in manifest.songs:
            parsed = _parse_text_file(data_dir, entry)
            artist = _upsert_artist(session, entry.artist_slug)
            source_record = session.scalar(
                select(SourceRecord).where(
                    SourceRecord.provider == PROVIDER,
                    SourceRecord.external_id == entry.external_id,
                )
            )
            if source_record is None:
                work = Work(
                    slug=_unique_work_slug(session, entry.title, entry.external_id),
                    title=entry.title,
                    normalized_title=normalize_search(entry.title),
                    publication_status="draft",
                )
                session.add(work)
                session.flush()
                session.add(
                    WorkArtist(
                        work_id=work.id,
                        artist_id=artist.id,
                        role="source_performer",
                        is_primary=True,
                    )
                )
                source_record = SourceRecord(
                    work_id=work.id,
                    provider=PROVIDER,
                    external_id=entry.external_id,
                    source_url=entry.url,
                    canonical_url=entry.canonical_url,
                    source_artist_slug=entry.artist_slug,
                    source_title=entry.title,
                    captured_at=entry.captured_at,
                    content_hash=parsed.body_hash,
                    source_file=entry.file,
                    rights_status="unknown",
                )
                session.add(source_record)
                session.flush()
            else:
                if source_record.source_artist_slug != entry.artist_slug:
                    raise ImportDataError("source_identity_changed")
                source_record.source_url = entry.url
                source_record.canonical_url = entry.canonical_url
                source_record.source_artist_slug = entry.artist_slug
                source_record.source_title = entry.title
                source_record.captured_at = entry.captured_at
                source_record.content_hash = parsed.body_hash
                source_record.source_file = entry.file
                source_record.quality_flags.clear()

            for code, severity, details in parsed.flags:
                source_record.quality_flags.append(
                    QualityFlag(code=code, severity=severity, details=details)
                )
                flag_count += 1
            records_by_hash.setdefault(parsed.body_hash, []).append(source_record)
            imported += 1

        for records in records_by_hash.values():
            if len(records) < 2:
                continue
            ids = ",".join(sorted(record.external_id for record in records))
            for record in records:
                record.quality_flags.append(
                    QualityFlag(
                        code="exact_duplicate",
                        severity="warning",
                        details=f"provider_external_ids={ids}",
                    )
                )
                flag_count += 1

        run.imported = imported
        run.failed = manifest.failed
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        session.commit()
        return ImportSummary(
            run_id=run.id,
            status="completed",
            discovered=run.discovered,
            imported=run.imported,
            failed=run.failed,
            quality_flags=flag_count,
        )
    except Exception as error:
        session.rollback()
        _mark_run_failed(session, run.id, run.discovered or 1, type(error).__name__)
        if isinstance(
            error,
            (ImportDataError, UnicodeError, json.JSONDecodeError, ValidationError),
        ):
            raise ImportDataError("snapshot_validation_failed") from error
        raise


def _validate_uniqueness(manifest: Manifest) -> None:
    identities: set[tuple[str, str]] = set()
    files: set[str] = set()
    urls: set[str] = set()
    for song in manifest.songs:
        identity = (PROVIDER, song.external_id)
        if identity in identities:
            raise ImportDataError("duplicate_provider_external_id")
        if song.file in files:
            raise ImportDataError("duplicate_source_file")
        if song.url in urls:
            raise ImportDataError("duplicate_source_url")
        identities.add(identity)
        files.add(song.file)
        urls.add(song.url)


def _parse_text_file(data_dir: Path, entry: ManifestSong) -> ParsedTextFile:
    relative_path = PurePosixPath(entry.file)
    if (
        relative_path.is_absolute()
        or ".." in relative_path.parts
        or len(relative_path.parts) != 2
        or relative_path.suffix != ".txt"
        or relative_path.parts[0] != entry.artist_slug
    ):
        raise ImportDataError("unsafe_source_path")

    path = data_dir
    for part in relative_path.parts:
        path /= part
        if path.is_symlink():
            raise ImportDataError("source_symlink_rejected")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(data_dir.resolve(strict=True))
        text = resolved.read_bytes().decode("utf-8", errors="strict")
    except (OSError, ValueError) as error:
        raise ImportDataError("source_file_unavailable") from error

    header_text, separator, body_with_newline = text.partition("\n\n")
    if not separator:
        raise ImportDataError("invalid_source_wrapper")
    header_lines = header_text.split("\n")
    if len(header_lines) != 6:
        raise ImportDataError("invalid_source_header_count")

    headers: dict[str, str] = {}
    for line in header_lines:
        key, delimiter, value = line.partition(": ")
        if not delimiter or key in headers:
            raise ImportDataError("invalid_source_header")
        headers[key] = value
    expected_keys = {"Titulo", "Artista", "Fuente", "ID fuente", "Capturado", "SHA-256"}
    if set(headers) != expected_keys:
        raise ImportDataError("invalid_source_header_keys")

    body = body_with_newline[:-1] if body_with_newline.endswith("\n") else body_with_newline
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    expected_headers = {
        "Titulo": entry.title,
        "Artista": entry.artist_slug,
        "Fuente": entry.url,
        "ID fuente": entry.external_id,
        "Capturado": entry.captured_at.isoformat(),
        "SHA-256": entry.content_hash,
    }
    if headers != expected_headers or body_hash != entry.content_hash:
        raise ImportDataError("source_integrity_mismatch")

    flags: list[tuple[str, str, str | None]] = []
    if "Ã" in body or "Â" in body:
        flags.append(("mojibake", "warning", None))
    controls = sorted({f"U+{ord(char):04X}" for char in body if 0x7F <= ord(char) <= 0x9F})
    if controls:
        flags.append(("control_character", "warning", ",".join(controls)))
    return ParsedTextFile(body_hash=body_hash, flags=tuple(flags))


def _upsert_artist(session: Session, artist_slug: str) -> Artist:
    artist = session.scalar(select(Artist).where(Artist.slug == artist_slug))
    if artist is None:
        artist = Artist(
            slug=artist_slug,
            name=ARTIST_NAMES.get(artist_slug, artist_slug.replace("-", " ").title()),
            publication_status="draft",
        )
        session.add(artist)
        session.flush()
    return artist


def _unique_work_slug(session: Session, title: str, external_id: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    ascii_title = normalized.encode("ascii", "ignore").decode("ascii").casefold()
    base = re.sub(r"[^a-z0-9]+", "-", ascii_title).strip("-")[:150] or "obra"
    if session.scalar(select(Work.id).where(Work.slug == base)) is None:
        return base

    digest = hashlib.sha256(external_id.encode("utf-8")).hexdigest()[:12]
    attempt = 0
    while True:
        suffix = digest if attempt == 0 else f"{digest}-{attempt}"
        candidate = f"{base[: 179 - len(suffix)]}-{suffix}"
        if session.scalar(select(Work.id).where(Work.slug == candidate)) is None:
            return candidate
        attempt += 1


def _mark_run_failed(
    session: Session,
    run_id: str,
    failed: int,
    error_code: str,
) -> None:
    try:
        failed_run = session.get(ImportRun, run_id)
        if failed_run:
            failed_run.status = "failed"
            failed_run.failed = failed
            failed_run.completed_at = datetime.now(timezone.utc)
            failed_run.error_code = error_code[:80]
            session.commit()
    except Exception:
        session.rollback()
