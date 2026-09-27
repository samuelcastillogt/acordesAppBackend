from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Settings, get_settings
from .database import Base, create_database_engine, create_session_factory
from .models import (
    ApiError,
    ArtistSummary,
    CatalogStats,
    ContentReportCreate,
    ContentReportResponse,
    ErrorResponse,
    HealthResponse,
    Pagination,
    ReadinessResponse,
    SearchResponse,
    WorkListResponse,
    WorkSheetResponse,
    WorkSummary,
)
from .repository import CatalogRepository
from .sheet_service import SheetUnavailableError, read_verified_sheet


OPENAPI_TAGS = [
    {"name": "system", "description": "Liveness and database readiness."},
    {"name": "catalog", "description": "Public, reviewed catalog projection."},
    {"name": "feedback", "description": "Content corrections and rights reports."},
]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    engine = create_database_engine(resolved_settings.database_url)
    session_factory = create_session_factory(engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.auto_create_schema:
            Base.metadata.create_all(engine)
        yield
        engine.dispose()

    api = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.api_version,
        summary="API publica del universo Soda Stereo y Gustavo Cerati.",
        description=(
            "Expone entidades revisadas con estado `public` y sus tablaturas verificadas. "
            "Los paths, hashes y registros internos de las fuentes permanecen privados."
        ),
        contact={"name": "Equipo editorial"},
        license_info={"name": "Contenido sujeto a derechos y fuentes declaradas"},
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    api.state.settings = resolved_settings
    api.state.engine = engine
    api.state.session_factory = session_factory

    api.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )
    register_middleware(api)
    register_exception_handlers(api)
    register_routes(api)
    return api


def register_middleware(api: FastAPI) -> None:
    @api.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id[:128]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


def register_exception_handlers(api: FastAPI) -> None:
    @api.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "not_found" if exc.status_code == 404 else "http_error"
        return _error_response(request, exc.status_code, code, str(exc.detail), exc.headers)

    @api.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"location": ".".join(str(item) for item in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return _error_response(
            request,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "validation_error",
            "Los parametros enviados no son validos.",
            details=details,
        )

    @api.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _: Exception) -> JSONResponse:
        return _error_response(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "Ocurrio un error inesperado.",
        )


def register_routes(api: FastAPI) -> None:
    error_responses = {
        404: {"model": ErrorResponse, "description": "Recurso no encontrado."},
        422: {"model": ErrorResponse, "description": "Parametros invalidos."},
        500: {"model": ErrorResponse, "description": "Error inesperado."},
    }

    @api.get("/health/live", response_model=HealthResponse, tags=["system"])
    def liveness(request: Request) -> HealthResponse:
        settings: Settings = request.app.state.settings
        return HealthResponse(status="ok", service=settings.app_name, version=settings.api_version)

    @api.get(
        "/health/ready",
        response_model=ReadinessResponse,
        tags=["system"],
        responses={503: {"model": ErrorResponse}},
    )
    def readiness(
        request: Request,
        session: Annotated[Session, Depends(get_session)],
    ) -> ReadinessResponse:
        try:
            session.execute(text("SELECT 1"))
        except Exception as error:
            raise HTTPException(status_code=503, detail="La base de datos no esta disponible.") from error
        settings: Settings = request.app.state.settings
        return ReadinessResponse(
            status="ok",
            service=settings.app_name,
            version=settings.api_version,
            database="ready",
        )

    @api.get(
        "/api/v1/catalog/stats",
        response_model=CatalogStats,
        tags=["catalog"],
    )
    def catalog_stats(
        repository: Annotated[CatalogRepository, Depends(get_repository)],
    ) -> CatalogStats:
        return repository.stats()

    @api.get(
        "/api/v1/artists",
        response_model=list[ArtistSummary],
        tags=["catalog"],
    )
    def list_artists(
        repository: Annotated[CatalogRepository, Depends(get_repository)],
    ) -> list[ArtistSummary]:
        return repository.list_artists()

    @api.get(
        "/api/v1/artists/{artist_slug}",
        response_model=ArtistSummary,
        tags=["catalog"],
        responses=error_responses,
    )
    def get_artist(
        repository: Annotated[CatalogRepository, Depends(get_repository)],
        artist_slug: Annotated[str, Path(pattern=r"^[a-z0-9-]+$")],
    ) -> ArtistSummary:
        artist = repository.get_artist(artist_slug)
        if artist is None:
            raise HTTPException(status_code=404, detail="No se encontro el artista solicitado.")
        return artist

    @api.get(
        "/api/v1/works",
        response_model=WorkListResponse,
        tags=["catalog"],
        responses=error_responses,
    )
    def list_works(
        repository: Annotated[CatalogRepository, Depends(get_repository)],
        artist_slug: Annotated[str | None, Query(pattern=r"^[a-z0-9-]+$")] = None,
        q: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> WorkListResponse:
        items, total = repository.list_works(
            artist_slug=artist_slug,
            query=q,
            limit=limit,
            offset=offset,
        )
        return WorkListResponse(
            items=items,
            pagination=Pagination(
                limit=limit,
                offset=offset,
                total=total,
                next_offset=offset + limit if offset + limit < total else None,
            ),
        )

    @api.get(
        "/api/v1/works/{work_slug}",
        response_model=WorkSummary,
        tags=["catalog"],
        responses=error_responses,
    )
    def get_work(
        repository: Annotated[CatalogRepository, Depends(get_repository)],
        work_slug: Annotated[str, Path(pattern=r"^[a-z0-9-]+$")],
    ) -> WorkSummary:
        work = repository.get_work(work_slug)
        if work is None:
            raise HTTPException(status_code=404, detail="No se encontro la obra solicitada.")
        return work

    @api.get(
        "/api/v1/works/{work_slug}/sheet",
        response_model=WorkSheetResponse,
        tags=["catalog"],
        responses=error_responses,
    )
    def get_work_sheet(
        request: Request,
        repository: Annotated[CatalogRepository, Depends(get_repository)],
        work_slug: Annotated[str, Path(pattern=r"^[a-z0-9-]+$")],
    ) -> WorkSheetResponse:
        source = repository.get_public_source(work_slug)
        if source is None:
            raise HTTPException(status_code=404, detail="No se encontro la tablatura solicitada.")
        try:
            content = read_verified_sheet(request.app.state.settings.data_dir, source)
        except SheetUnavailableError as error:
            raise HTTPException(
                status_code=503,
                detail="La tablatura no esta disponible temporalmente.",
            ) from error
        return WorkSheetResponse(work_slug=work_slug, content=content)

    @api.get(
        "/api/v1/search",
        response_model=SearchResponse,
        tags=["catalog"],
        responses=error_responses,
    )
    def search(
        repository: Annotated[CatalogRepository, Depends(get_repository)],
        q: Annotated[str, Query(min_length=2, max_length=120)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
    ) -> SearchResponse:
        works, _ = repository.list_works(
            artist_slug=None, query=q, limit=limit, offset=0
        )
        return SearchResponse(query=q, works=works)

    @api.post(
        "/api/v1/reports",
        response_model=ContentReportResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["feedback"],
        responses={422: error_responses[422], 500: error_responses[500]},
    )
    def create_report(
        payload: ContentReportCreate,
        repository: Annotated[CatalogRepository, Depends(get_repository)],
    ) -> ContentReportResponse:
        return repository.create_report(payload)


def get_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    with factory() as session:
        yield session


def get_repository(
    session: Annotated[Session, Depends(get_session)],
) -> CatalogRepository:
    return CatalogRepository(session)


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
    details: list[dict[str, object]] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content=ErrorResponse(
            error=ApiError(
                code=code,
                message=message,
                request_id=request_id,
                details=details,
            )
        ).model_dump(mode="json"),
    )


app = create_app()
