import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select

import app.importer as importer_module
from app.database import Base, create_database_engine, create_session_factory
from app.db_models import Artist, ImportRun, QualityFlag, SourceRecord, Work
from app.importer import import_snapshot


def test_importer_validates_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "snapshot"
    artist_dir = data_dir / "soda-stereo"
    artist_dir.mkdir(parents=True)
    captured_at = datetime(2026, 1, 2, tzinfo=timezone.utc).isoformat()
    body = "Intro:\n\nBm-G-D-A\nTexto sintetico"
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    url = "https://acordesweb.com/cancion/soda-stereo/obra-sintetica"
    source_file = artist_dir / "obra-sintetica.txt"
    source_file.write_text(
        "\n".join(
            [
                "Titulo: Obra Sintetica",
                "Artista: soda-stereo",
                f"Fuente: {url}",
                "ID fuente: 1",
                f"Capturado: {captured_at}",
                f"SHA-256: {body_hash}",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    manifest = {
        "generated_at": captured_at,
        "source": "https://acordesweb.com",
        "artists": ["soda-stereo"],
        "discovered": 2,
        "saved": 1,
        "failed": 1,
        "songs": [
            {
                "artist_slug": "soda-stereo",
                "title": "Obra Sintetica",
                "url": url,
                "canonical_url": url,
                "external_id": "1",
                "notation": "0",
                "captured_at": captured_at,
                "content_hash": body_hash,
                "file": "soda-stereo/obra-sintetica.txt",
            }
        ],
        "failures": [{"url": "https://acordesweb.com/fallida", "error": "timeout"}],
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    engine = create_database_engine(f"sqlite:///{tmp_path / 'import.db'}")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        first = import_snapshot(session, data_dir)
        second = import_snapshot(session, data_dir)

        assert first.imported == 1
        assert second.imported == 1
        assert first.failed == 1
        assert second.failed == 1
        assert session.scalar(select(func.count()).select_from(SourceRecord)) == 1
        assert session.scalar(select(func.count()).select_from(Work)) == 1
        assert session.scalar(select(func.count()).select_from(ImportRun)) == 2
        assert session.scalar(select(func.count()).select_from(QualityFlag)) == 0
        assert session.scalar(select(Work.publication_status)) == "draft"
        assert session.scalar(select(Artist.publication_status)) == "draft"

        def fail_unexpectedly(*_: object) -> None:
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(importer_module, "_parse_text_file", fail_unexpectedly)
        with pytest.raises(RuntimeError, match="synthetic failure"):
            importer_module.import_snapshot(session, data_dir)

        session.expire_all()
        failed_run = session.scalar(
            select(ImportRun).where(ImportRun.status == "failed")
        )
        assert failed_run is not None
        assert failed_run.error_code == "RuntimeError"

    engine.dispose()
