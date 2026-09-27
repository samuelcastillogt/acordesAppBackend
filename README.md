# Backend Soda/Cerati

API FastAPI para el catálogo público y el proceso privado de importación. La API
falla de forma cerrada: un registro importado comienza en `draft` y no aparece en
los endpoints públicos hasta que un proceso editorial cambie su estado a
`public`.

## Stack

- FastAPI y Pydantic para REST/OpenAPI.
- SQLAlchemy 2 para persistencia.
- SQLite en desarrollo; PostgreSQL mediante `DATABASE_URL` en producción.
- Pytest para contratos HTTP e importación.

## Instalación

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Las variables de `.env` deben exportarse en la shell o cargarse desde el sistema
de despliegue. La aplicación no lee secretos de archivos por sí sola.

## Importar el snapshot privado

```bash
python -m app.import_catalog --data-dir ../acordesSoda/scraped_songs
```

El importador:

- valida el schema y los conteos del manifiesto;
- rechaza paths inseguros y symlinks;
- comprueba las seis cabeceras de cada TXT;
- verifica UTF-8 y SHA-256 del cuerpo;
- registra mojibake, controles y duplicados exactos;
- guarda solo metadata en la base de datos; el cuerpo permanece en el TXT fuente;
- crea obras en estado `draft` y derechos `unknown`;
- es idempotente por `(provider, external_id)`.

## Ejecutar

```bash
uvicorn app.main:app --reload --port 8000
```

Documentación interactiva:

- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- OpenAPI JSON: http://127.0.0.1:8000/openapi.json

## Endpoints

- `GET /health/live`
- `GET /health/ready`
- `GET /api/v1/catalog/stats`
- `GET /api/v1/artists`
- `GET /api/v1/artists/{artist_slug}`
- `GET /api/v1/works`
- `GET /api/v1/works/{work_slug}`
- `GET /api/v1/works/{work_slug}/sheet`
- `GET /api/v1/search?q=`
- `POST /api/v1/reports`

Los endpoints de catálogo filtran siempre por `publication_status = public`.
La lectura de tablaturas vuelve a validar el path, los symlinks y el SHA-256 del
TXT antes de responder. `source_records`, hashes, paths y quality flags no forman
parte del contrato público.

## Pruebas

```bash
pytest -q
```

Antes de producción se debe incorporar una herramienta de migraciones como
Alembic y desactivar `SODA_AUTO_CREATE_SCHEMA`.
