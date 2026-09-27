from __future__ import annotations

import unicodedata

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from .db_models import Artist, ContentReport, SourceRecord, Work, WorkArtist
from .models import (
    ArtistSummary,
    CatalogStats,
    ContentReportCreate,
    ContentReportResponse,
    WorkSummary,
)


PUBLIC = "public"


def normalize_search(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join(
        normalized.encode("ascii", "ignore").decode("ascii").casefold().split()
    )


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def stats(self) -> CatalogStats:
        public_artists = self.session.scalar(
            select(func.count()).select_from(Artist).where(
                Artist.publication_status == PUBLIC
            )
        ) or 0
        public_works = self.session.scalar(
            select(func.count()).select_from(Work).where(
                self._is_public_work()
            )
        ) or 0
        last_public_update = self.session.scalar(
            select(func.max(Work.updated_at)).where(self._is_public_work())
        )
        return CatalogStats(
            public_artists=public_artists,
            public_works=public_works,
            last_public_update=last_public_update,
        )

    def list_artists(self) -> list[ArtistSummary]:
        statement = (
            select(Artist, func.count(Work.id))
            .outerjoin(WorkArtist, WorkArtist.artist_id == Artist.id)
            .outerjoin(
                Work,
                (Work.id == WorkArtist.work_id) & (Work.publication_status == PUBLIC),
            )
            .where(Artist.publication_status == PUBLIC)
            .group_by(Artist.id)
            .order_by(Artist.name)
        )
        return [
            ArtistSummary(
                slug=artist.slug,
                name=artist.name,
                summary=artist.summary,
                work_count=work_count,
            )
            for artist, work_count in self.session.execute(statement)
        ]

    def get_artist(self, slug: str) -> ArtistSummary | None:
        statement = (
            select(Artist, func.count(Work.id))
            .outerjoin(WorkArtist, WorkArtist.artist_id == Artist.id)
            .outerjoin(
                Work,
                (Work.id == WorkArtist.work_id) & (Work.publication_status == PUBLIC),
            )
            .where(Artist.slug == slug, Artist.publication_status == PUBLIC)
            .group_by(Artist.id)
        )
        row = self.session.execute(statement).one_or_none()
        if row is None:
            return None
        artist, work_count = row
        return ArtistSummary(
            slug=artist.slug,
            name=artist.name,
            summary=artist.summary,
            work_count=work_count,
        )

    def list_works(
        self,
        *,
        artist_slug: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkSummary], int]:
        statement = self._public_works_statement()
        if artist_slug:
            statement = statement.join(Work.artists).join(WorkArtist.artist).where(
                Artist.slug == artist_slug,
                Artist.publication_status == PUBLIC,
            )
        if query:
            needle = normalize_search(query)
            if not needle:
                return [], 0
            statement = statement.where(
                or_(
                    Work.normalized_title.contains(needle),
                    Work.slug.contains(needle.replace(" ", "-")),
                )
            )

        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        total = self.session.scalar(count_statement) or 0
        works = self.session.scalars(
            statement.order_by(Work.normalized_title, Work.id).limit(limit).offset(offset)
        ).unique()
        return [self._to_work_summary(work) for work in works], total

    def get_work(self, slug: str) -> WorkSummary | None:
        work = self.session.scalar(self._public_works_statement().where(Work.slug == slug))
        return self._to_work_summary(work) if work else None

    def get_public_source(self, slug: str) -> SourceRecord | None:
        return self.session.scalar(
            select(SourceRecord)
            .join(SourceRecord.work)
            .where(Work.slug == slug, self._is_public_work())
            .order_by(SourceRecord.captured_at.desc(), SourceRecord.id.desc())
            .limit(1)
        )

    def create_report(self, payload: ContentReportCreate) -> ContentReportResponse:
        report = ContentReport(**payload.model_dump())
        self.session.add(report)
        self.session.commit()
        self.session.refresh(report)
        return ContentReportResponse(
            id=report.id,
            status="open",
            created_at=report.created_at,
        )

    @staticmethod
    def _public_works_statement() -> Select[tuple[Work]]:
        return (
            select(Work)
            .where(CatalogRepository._is_public_work())
            .options(selectinload(Work.artists).selectinload(WorkArtist.artist))
        )

    @staticmethod
    def _is_public_work() -> ColumnElement[bool]:
        return (
            (Work.publication_status == PUBLIC)
            & Work.artists.any(
                WorkArtist.artist.has(Artist.publication_status == PUBLIC)
            )
        )

    @staticmethod
    def _to_work_summary(work: Work) -> WorkSummary:
        artists = [
            ArtistSummary(
                slug=link.artist.slug,
                name=link.artist.name,
                summary=link.artist.summary,
                work_count=0,
            )
            for link in sorted(work.artists, key=lambda item: (not item.is_primary, item.artist.name))
            if link.artist.publication_status == PUBLIC
        ]
        return WorkSummary(
            slug=work.slug,
            title=work.title,
            summary=work.summary,
            artists=artists,
            updated_at=work.updated_at,
        )
