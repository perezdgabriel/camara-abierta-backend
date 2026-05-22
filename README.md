# Camara Abierta

Backend principal para una plataforma de transparencia legislativa enfocada en proyectos de ley, legisladores, votaciones, Diario Oficial y reglamentos CGR. Construido sobre FastAPI, SQLAlchemy 2, Alembic y Celery, con una arquitectura modular organizada por dominio.

## Alcance v0.1

La version actual prioriza cinco superficies de producto:

- Seguimiento de proyectos de ley (`/api/v1/bills`)
- Directorio de legisladores (`/api/v1/legislators`)
- Sesiones de votacion (`/api/v1/voting-sessions`)
- Diario Oficial (`/api/v1/diario-oficial`)
- Reglamentos CGR (`/api/v1/reglamentos`)

La busqueda con Elasticsearch y el sync orientado a clientes se mantienen en codigo, pero no forman parte de los flujos principales de v0.1.

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

GET /api/v1/diario-oficial/normas
GET /api/v1/reglamentos
GET /api/v1/health
```

Este repositorio ahora concentra la API, los scrapers de Diario Oficial y CGR, los ingestors legislativos y los workers asincronicos. Redis actua como broker/result backend de Celery; PostgreSQL es la fuente de verdad; Elasticsearch mantiene la indexacion full text de proyectos de ley.

## Estructura del proyecto

```
app/
├── api/
│   └── v1/
│       ├── diario_oficial.py   # /api/v1/diario-oficial/*
│       ├── reglamentos.py      # /api/v1/reglamentos/*
│       ├── proyectos.py        # /api/v1/proyectos/* (en desarrollo)
│       └── sync.py             # /api/v1/sync/*     (en desarrollo)
├── core/
│   ├── config.py               # Settings via pydantic-settings
│   ├── database.py             # Engine, sesion y Base declarativa
│   ├── session.py              # task_session() para workers Celery
│   ├── celery_app.py           # App Celery para workers
│   └── celery_beat.py          # Beat schedule
├── ingestors/                  # Clientes/parsers para APIs del Congreso
├── models/
│   ├── base.py                 # Mixins: SyncableMixin, TimestampMixin, etc.
│   ├── diario_oficial.py       # NormaGeneral, Reglamento, ReglamentoEtapa
│   ├── ingestor_state.py       # Estado incremental de ingestors
│   ├── core.py                 # Geografía y temas (Region, Commune, Topic…)
│   ├── legislature.py          # Partido, Legislador, Camara, Comision…
│   ├── proyecto.py             # Bill, BillStage, BillDocument…
│   ├── votacion.py             # VotingSession, Vote, LegislatorVotingStats
│   └── sync.py                 # ClientSyncState, ChangeLog
├── scrapers/                   # Scrapers browser-driven de Diario Oficial y CGR
├── schemas/                    # Esquemas Pydantic de entrada/salida
├── services/                   # Logica de lectura, escritura, LLM, PDF y notificaciones
└── tasks/                      # Tareas Celery periodicas y por-item
migrations/
└── versions/
  └── ...
```

## Ejecución local

```bash
# Variables de entorno
# Crear .env.local con al menos:
# DATABASE_URL=postgresql://postgres:postgres@localhost:5432/camara_abierta
# REDIS_URL=redis://localhost:6379/0
# ELASTICSEARCH_URL=http://localhost:9200

# Instalar dependencias
uv sync

# API
uvicorn app.main:app --reload

# Worker Celery
celery -A app.core.celery_app worker -Q default,llm -c 4 --loglevel=info

# Beat scheduler
celery -A app.core.celery_beat beat --loglevel=info

# CLI manual de scrapers e ingestors
python -m app.cli list
python -m app.cli scrapers diario-oficial --target-date 2026-05-04 --dry-run
python -m app.cli scrapers cgr-reglamentos --dry-run
python -m app.cli ingestors bills --since 2026-05-01 --dry-run
python -m app.cli ingestors legislators
```

Documentación interactiva disponible en [http://localhost:8000/docs](http://localhost:8000/docs).

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

## CLI de scrapers e ingestors

```bash
# Ver los jobs disponibles
python -m app.cli list

# Scrapers
python -m app.cli scrapers diario-oficial --target-date 2026-05-04
python -m app.cli scrapers cgr-reglamentos --dry-run

# Ingestors
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

Servicios incluidos: `api`, `celery-worker`, `celery-beat`, `postgres`, `redis`, `elasticsearch`.

## Endpoints disponibles

### Diario Oficial

| Método | Ruta                                            | Descripción                           |
| ------ | ----------------------------------------------- | ------------------------------------- |
| `GET`  | `/api/v1/diario-oficial/normas`                 | Lista normas con filtros y paginación |
| `GET`  | `/api/v1/diario-oficial/normas/{cve}`           | Norma por CVE                         |
| `GET`  | `/api/v1/diario-oficial/normas/por-importancia` | Normas destacadas                     |
| `GET`  | `/api/v1/diario-oficial/dates/available`        | Fechas con publicaciones              |
| `GET`  | `/api/v1/diario-oficial/stats/by-ministry`      | Conteo por ministerio                 |

Parámetros de filtro para `/normas`: `date_from`, `date_to`, `ministry`, `branch`, `search`, `offset`, `limit` (máx 500).

### Reglamentos CGR

| Método | Ruta                                           | Descripción                          |
| ------ | ---------------------------------------------- | ------------------------------------ |
| `GET`  | `/api/v1/reglamentos`                          | Lista reglamentos con filtros        |
| `GET`  | `/api/v1/reglamentos/{id}`                     | Detalle de un reglamento             |
| `GET`  | `/api/v1/reglamentos/recientes`                | Último estado de reglamentos activos |
| `GET`  | `/api/v1/reglamentos/stats/por-ministerio`     | Conteo por ministerio                |
| `GET`  | `/api/v1/reglamentos/stats/por-categoria`      | Conteo por categoría                 |
| `GET`  | `/api/v1/reglamentos/stats/tiempo-tramitacion` | Tiempo promedio de tramitación       |
| `GET`  | `/api/v1/reglamentos/stats/mas-etapas`         | Reglamentos con más etapas           |

### Proyectos de ley

| Método | Ruta                              | Descripción                            |
| ------ | --------------------------------- | -------------------------------------- |
| `GET`  | `/api/v1/bills`                   | Lista proyectos de ley con filtros     |
| `GET`  | `/api/v1/bills/{id}`              | Detalle de un proyecto                 |
| `GET`  | `/api/v1/bills/{id}/etapas`       | Etapas del proyecto                    |
| `GET`  | `/api/v1/bills/{id}/votaciones`   | Votaciones asociadas al proyecto       |
| `GET`  | `/api/v1/bills/{id}/documentos`   | Documentos asociados al proyecto       |

### Legisladores y votaciones

| Método | Ruta                              | Descripción                            |
| ------ | --------------------------------- | -------------------------------------- |
| `GET`  | `/api/v1/legislators`             | Lista legisladores con filtros         |
| `GET`  | `/api/v1/legislators/{id}`        | Detalle de legislador                  |
| `GET`  | `/api/v1/voting-sessions`         | Lista sesiones de votación             |
| `GET`  | `/api/v1/voting-sessions/{id}`    | Detalle de sesión con votos            |

### Sync

| Método | Ruta                              | Descripción                            |
| ------ | --------------------------------- | -------------------------------------- |
| `GET`  | `/api/v1/sync/normas`             | Delta sync de normas                   |
| `GET`  | `/api/v1/sync/reglamentos`        | Delta sync de reglamentos              |

## Variables de entorno

| Variable                            | Requerida | Descripción                              |
| ----------------------------------- | --------- | ---------------------------------------- |
| `DATABASE_URL`                      | ✅        | URL de conexión PostgreSQL               |
| `REDIS_URL`                         | ✅        | Broker y result backend de Celery        |
| `ELASTICSEARCH_URL`                 | ✅        | URL de Elasticsearch                     |
| `GEMINI_API_KEY`                    | Opcional  | Clave Gemini para analisis de normas     |
| `OPENWEBUI_URL`                     | Opcional  | Endpoint Open WebUI para analisis de PDF |
| `OPENWEBUI_API_KEY`                 | Opcional  | Token Open WebUI                         |
| `OPENWEBUI_MODEL`                   | Opcional  | Modelo Open WebUI                        |
| `RESEND_API_KEY`                    | Opcional  | Clave Resend para alertas                |
| `NOTIFICATION_EMAIL`                | Opcional  | Destinatario de alertas                  |
| `NOTIFICATION_FROM_EMAIL`           | Opcional  | Remitente de alertas                     |
| `INGESTOR_BASE_URL_CAMARA`          | Opcional  | Override base URL API Camara legacy      |
| `INGESTOR_BASE_URL_OPENDATA_CAMARA` | Opcional  | Override base URL OpenData Camara        |
| `INGESTOR_BASE_URL_SENADO`          | Opcional  | Override base URL API Senado             |
