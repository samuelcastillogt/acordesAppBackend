import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db_models import Artist, SourceRecord, Work, WorkArtist
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    data_dir = tmp_path / "snapshot"
    artist_dir = data_dir / "soda-stereo"
    artist_dir.mkdir(parents=True)
    sheet_content = "Intro:\n\nBm-G-D-A\nTexto sintetico"
    sheet_hash = hashlib.sha256(sheet_content.encode("utf-8")).hexdigest()
    (artist_dir / "de-musica-ligera.txt").write_text(
        f"Cabecera: prueba\n\n{sheet_content}\n",
        encoding="utf-8",
    )
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        data_dir=data_dir,
        auto_create_schema=True,
        cors_origins=["http://localhost:3000"],
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        factory = app.state.session_factory
        with factory() as session:
            artist = Artist(
                slug="soda-stereo",
                name="Soda Stereo",
                summary="Banda argentina.",
                publication_status="public",
            )
            session.add(artist)
            session.flush()
            draft_artist = Artist(
                slug="artista-privado",
                name="Artista privado",
                publication_status="draft",
            )
            session.add(draft_artist)
            session.flush()
            public_work = Work(
                slug="de-musica-ligera",
                title="De Música Ligera",
                normalized_title="de musica ligera",
                summary="Obra publicada para pruebas.",
                publication_status="public",
            )
            draft_work = Work(
                slug="obra-privada",
                title="Obra privada",
                normalized_title="obra privada",
                publication_status="draft",
            )
            private_artist_work = Work(
                slug="obra-con-artista-privado",
                title="Obra con artista privado",
                normalized_title="obra con artista privado",
                publication_status="public",
            )
            session.add_all([public_work, draft_work, private_artist_work])
            session.flush()
            session.add_all(
                [
                    WorkArtist(work_id=public_work.id, artist_id=artist.id),
                    WorkArtist(work_id=draft_work.id, artist_id=artist.id),
                    WorkArtist(work_id=private_artist_work.id, artist_id=draft_artist.id),
                ]
            )
            session.add(
                SourceRecord(
                    work_id=public_work.id,
                    provider="test",
                    external_id="sheet-1",
                    source_url="https://example.com/source",
                    canonical_url="https://example.com/source",
                    source_artist_slug="soda-stereo",
                    source_title="De Música Ligera",
                    captured_at=datetime.now(timezone.utc),
                    content_hash=sheet_hash,
                    source_file="soda-stereo/de-musica-ligera.txt",
                    rights_status="reviewed",
                )
            )
            session.commit()
        yield test_client


def test_health_checks_do_not_expose_filesystem(client: TestClient) -> None:
    live = client.get("/health/live")
    ready = client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["database"] == "ready"
    assert "data_source" not in live.json()


def test_catalog_only_returns_public_entities(client: TestClient) -> None:
    artists = client.get("/api/v1/artists")
    works = client.get("/api/v1/works")

    assert artists.status_code == 200
    assert artists.json()[0]["work_count"] == 1
    assert works.status_code == 200
    assert works.json()["pagination"]["total"] == 1
    assert works.json()["items"][0]["slug"] == "de-musica-ligera"
    assert client.get("/api/v1/works/obra-privada").status_code == 404
    assert client.get("/api/v1/works/obra-con-artista-privado").status_code == 404
    assert client.get("/api/v1/artists/artista-privado").status_code == 404


def test_search_is_accent_tolerant_and_empty_normalization_is_safe(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/search", params={"q": "musica ligera"})
    emoji = client.get("/api/v1/search", params={"q": "🎸🎸"})

    assert response.status_code == 200
    assert response.json()["works"][0]["slug"] == "de-musica-ligera"
    assert emoji.status_code == 200
    assert emoji.json()["works"] == []


def test_public_sheet_is_verified_without_source_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/works/de-musica-ligera/sheet")

    assert response.status_code == 200
    assert response.json() == {
        "work_slug": "de-musica-ligera",
        "content": "Intro:\n\nBm-G-D-A\nTexto sintetico",
    }
    assert "source_file" not in response.text
    assert client.get("/api/v1/works/obra-privada/sheet").status_code == 404


def test_validation_and_not_found_use_error_envelope(client: TestClient) -> None:
    invalid = client.get("/api/v1/works", params={"limit": 1000})
    missing = client.get("/api/v1/works/no-existe")
    unknown_route = client.get("/api/v1/no-existe")

    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    assert invalid.headers["x-request-id"]
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert unknown_route.status_code == 404
    assert unknown_route.json()["error"]["code"] == "not_found"


def test_content_report_is_created(client: TestClient) -> None:
    response = client.post(
        "/api/v1/reports",
        json={
            "entity_type": "work",
            "entity_slug": "de-musica-ligera",
            "reason": "incorrect",
            "message": "El año publicado necesita revisión editorial.",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "open"


def test_openapi_documents_public_contract(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert "/api/v1/works" in schema["paths"]
    assert "/api/v1/search" in schema["paths"]
    assert "/api/v1/reports" in schema["paths"]
    assert schema["info"]["version"] == "0.2.0"
