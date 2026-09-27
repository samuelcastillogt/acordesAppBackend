from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "acordesSoda" / "scraped_songs"


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _as_bool(value: str) -> bool:
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = "Soda/Cerati Catalog API"
    api_version: str = "0.2.0"
    environment: str = "development"
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'soda.db'}"
    data_dir: Path = DEFAULT_DATA_DIR
    auto_create_schema: bool = True
    cors_origins: list[str] = field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ]
    )

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            app_name=os.getenv("SODA_API_NAME", cls.app_name),
            api_version=os.getenv("SODA_API_VERSION", cls.api_version),
            environment=os.getenv("SODA_ENVIRONMENT", cls.environment),
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            data_dir=Path(
                os.getenv("SODA_DATA_DIR", str(DEFAULT_DATA_DIR))
            ).expanduser(),
            auto_create_schema=_as_bool(
                os.getenv("SODA_AUTO_CREATE_SCHEMA", "true")
            ),
            cors_origins=_split_csv(
                os.getenv(
                    "SODA_CORS_ORIGINS",
                    "http://localhost:3000,http://127.0.0.1:3000",
                )
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
