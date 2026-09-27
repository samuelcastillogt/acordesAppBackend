from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    LICENSED = "licensed"
    PUBLIC = "public"
    BLOCKED = "blocked"
    REMOVED = "removed"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ApiError(ApiModel):
    code: str = Field(examples=["not_found"])
    message: str = Field(examples=["No se encontro el recurso solicitado."])
    request_id: str
    details: list[dict[str, object]] | None = None


class ErrorResponse(ApiModel):
    error: ApiError


class HealthResponse(ApiModel):
    status: Literal["ok"]
    service: str
    version: str


class ReadinessResponse(HealthResponse):
    database: Literal["ready"]


class Pagination(ApiModel):
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


class ArtistSummary(ApiModel):
    slug: str = Field(pattern=r"^[a-z0-9-]+$", examples=["soda-stereo"])
    name: str = Field(examples=["Soda Stereo"])
    summary: str | None = None
    work_count: int = Field(ge=0)


class WorkSummary(ApiModel):
    slug: str = Field(pattern=r"^[a-z0-9-]+$", examples=["de-musica-ligera"])
    title: str = Field(examples=["De Musica Ligera"])
    summary: str | None = None
    artists: list[ArtistSummary]
    updated_at: datetime


class WorkListResponse(ApiModel):
    items: list[WorkSummary]
    pagination: Pagination


class WorkSheetResponse(ApiModel):
    work_slug: str = Field(pattern=r"^[a-z0-9-]+$")
    content: str = Field(min_length=1)


class SearchResponse(ApiModel):
    query: str
    works: list[WorkSummary]


class CatalogStats(ApiModel):
    public_artists: int = Field(ge=0)
    public_works: int = Field(ge=0)
    last_public_update: datetime | None = None


class ContentReportCreate(ApiModel):
    entity_type: Literal["artist", "work", "story"]
    entity_slug: str = Field(pattern=r"^[a-z0-9-]+$", max_length=180)
    reason: Literal["copyright", "incorrect", "broken", "other"]
    message: str = Field(min_length=10, max_length=2000)
    contact_email: EmailStr | None = None


class ContentReportResponse(ApiModel):
    id: int
    status: Literal["open"]
    created_at: datetime


class ImportSummary(ApiModel):
    run_id: str
    status: Literal["completed", "failed"]
    discovered: int = Field(ge=0)
    imported: int = Field(ge=0)
    failed: int = Field(ge=0)
    quality_flags: int = Field(ge=0)
