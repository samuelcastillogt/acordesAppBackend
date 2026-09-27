from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import get_settings
from .database import Base, create_database_engine, create_session_factory
from .importer import ImportDataError, import_snapshot


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Validate and import private snapshot metadata."
    )
    parser.add_argument("--data-dir", type=Path, default=settings.data_dir)
    args = parser.parse_args()

    engine = create_database_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    try:
        with factory() as session:
            summary = import_snapshot(session, args.data_dir.resolve())
    except ImportDataError as error:
        print(json.dumps({"status": "failed", "code": str(error)}))
        return 1
    finally:
        engine.dispose()

    print(summary.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
