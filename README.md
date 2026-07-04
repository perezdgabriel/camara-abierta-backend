# Cámara Abierta — Backend

> Plataforma de transparencia legislativa para Chile: proyectos de ley, legisladores, votaciones, unificados en una sola API.

[![Python 3.14](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLAlchemy 2](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00)](https://www.sqlalchemy.org/)
[![Celery](https://img.shields.io/badge/Celery-37814A?logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)

## Sobre el proyecto

Los datos del Congreso de Chile existen, pero están repartidos entre varias APIs y sitios web con formatos, autenticaciones y calidades distintas. **Cámara Abierta** los recolecta, normaliza y enriquece en un único modelo de dominio consultable, para que ciudadanía, periodistas y desarrolladores puedan seguir la actividad legislativa sin pelear con cada fuente por separado.

Este repositorio es el backend: la API, los recolectores de datos (scrapers e ingestors), el enriquecimiento con LLM y los workers asíncronos que mantienen todo actualizado.

**Highlights técnicos:**

- **Pipeline de datos estricto y testeable** — `cliente → parser → task Celery → servicio de escritura`. Las mutaciones de base de datos ocurren en un único lugar (`services/write.py`), nunca dispersas en handlers o tareas.
- **Múltiples fuentes reconciliadas** — restsil, OpenData Cámara, BCN, senado.cl y scrapers browser-driven, con estrategias de failover configurables por variable de entorno.
- **Enriquecimiento con LLM** — resúmenes y clasificación temática de proyectos de ley con Claude.
- **Sync incremental** basado en una secuencia global de PostgreSQL (`sync_version`) para clientes móviles/offline.
- **ADRs** documentando decisiones de arquitectura, más un `CONTEXT.md` que captura el modelo de dominio.
- **Despliegue serverless en AWS** vía CDK (Lambda + RDS + migraciones Alembic).

## Stack

| Capa | Tecnología |
| --- | --- |
| API | FastAPI, Pydantic v2 |
| Datos | PostgreSQL, SQLAlchemy 2, Alembic |
| Async / jobs | Celery, Redis |
| Scraping | Playwright, httpx |
| LLM | Anthropic Claude, Google Gemini |
| Búsqueda | Elasticsearch (opcional) |
| Tooling | uv, Ruff, ty, pytest |
| Infra | Docker Compose (local), AWS CDK + Lambda (deploy) |

## Alcance v0.1

La version actual prioriza cinco superficies de producto:

- Seguimiento de proyectos de ley (`/api/v1/bills`)
- Directorio de legisladores (`/api/v1/legislators`)
- Sesiones de votacion (`/api/v1/voting-sessions`)

El sync orientado a clientes se mantiene en codigo, pero no forma parte de los flujos principales de v0.1.

## Endpoints principales

```text
GET /api/v1/bills
GET /api/v1/bills/{id}
GET /api/v1/bills/{id}/etapas
GET /api/v1/bills/{id}/votaciones
GET /api/v1/bills/{id}/documentos

GET /api/v1/legislators
GET /api/v1/legislators/{id}

GET /api/v1/voting-sessions
GET /api/v1/voting-sessions/{id}

GET /api/v1/health
```

Este repositorio ahora concentra la API, los ingestors legislativos y los workers asincronicos. Redis actua como broker/result backend de Celery; PostgreSQL es la fuente de verdad.

## Ejecución local

```bash
# Requiere Python 3.14

# Variables de entorno
# Crear .env.local con al menos:
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/camara_abierta
# REDIS_URL=redis://localhost:6379/0

# Instalar dependencias
uv sync

# API
uvicorn app.main:app --reload

# Worker Celery
celery -A app.core.celery_app worker -Q default,llm -c 4 --loglevel=info

# Beat scheduler
celery -A app.core.celery_beat beat --loglevel=info

# CLI manual de scrapers, ingestors y loaders
python -m app.cli list
python -m app.cli geography --dry-run
python -m app.cli scrapers diario-oficial --target-date 2026-05-04 --dry-run
python -m app.cli scrapers cgr-reglamentos --dry-run
python -m app.cli ingestors bills --since 2026-05-01 --dry-run
python -m app.cli ingestors legislators
```

Documentación interactiva disponible en [http://localhost:8000/docs](http://localhost:8000/docs).

## Tests

```bash
# Fast default test suite
uv run pytest

# PostgreSQL-backed integration tests
# TEST_DATABASE_URL must already exist, must differ from DATABASE_URL,
# and its database name must end with `_test`.
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/camara_abierta_test \
  uv run pytest -m integration --integration

# Convenience command
just test-integration
```

## Reset de base de datos y baseline Alembic

```bash
# Recrea por completo la base apuntada por DATABASE_URL,
# elimina migrations/versions/*.py,
# genera una nueva revision initial_schema y la aplica
python scripts/recreate_db.py

# En CI, scripts o terminales no interactivas
python scripts/recreate_db.py --yes
```

Durante esta etapa temprana del proyecto, el flujo recomendado es modificar los modelos SQLAlchemy y volver a ejecutar el script, en lugar de acumular migraciones intermedias. El resultado queda consolidado en una sola revision base `*_initial_schema.py`.

## CLI de scrapers, ingestors y loaders

```bash
# Ver los jobs disponibles
python -m app.cli list

# Geography baseline manual y versionado
python -m app.cli geography
python -m app.cli geography --dry-run

# Scrapers
python -m app.cli scrapers diario-oficial --target-date 2026-05-04
python -m app.cli scrapers cgr-reglamentos --dry-run

# Ingestors
python -m app.cli ingestors reference-data
python -m app.cli ingestors bills --since 2026-05-01 --dry-run
python -m app.cli ingestors bills --bulletin 17123-06
python -m app.cli ingestors voting-sessions --since 2026-05-03
```

`--dry-run` ejecuta la recoleccion y el parseo, pero no encola tareas downstream ni actualiza `ingestor_state`.

## Alembic

```bash
# Ver el estado actual
alembic current
```

## Docker

```bash
docker compose up --build
```

La imagen de Docker instala dependencias desde `pyproject.toml` y `uv.lock`, que ahora son la fuente de verdad del entorno de ejecucion.

Servicios incluidos: `api`, `celery-worker`, `celery-beat`, `postgres`, `redis`.

## Fuentes de datos y uso responsable

Cámara Abierta se construye exclusivamente sobre **datos públicos** del Estado de Chile: APIs abiertas del Congreso (Cámara de Diputados, Senado), la Biblioteca del Congreso Nacional (BCN), el Diario Oficial y la Contraloría General de la República. Los recolectores respetan los ritmos y límites de cada fuente (paginación acotada, concurrencia configurable, ejecución incremental) y no republican credenciales de acceso. Este proyecto no está afiliado ni respaldado por ninguna de esas instituciones.
