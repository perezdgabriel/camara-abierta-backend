# Diario Oficial de Chile — API

Backend principal para una plataforma de seguimiento legislativo, Diario Oficial y reglamentos CGR. Construido sobre FastAPI, SQLAlchemy 2 y Alembic, con una arquitectura modular organizada por dominio.

Los datos de Diario Oficial y CGR siguen llegando desde los repositorios de scraping y worker existentes. El dominio legislativo (cámaras, legisladores, proyectos de ley, votaciones) y el delta sync se incorporan en este repositorio.

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
│   └── database.py             # Engine, sesión y Base declarativa
├── models/
│   ├── base.py                 # Mixins: SyncableMixin, TimestampMixin, etc.
│   ├── diario_oficial.py       # NormaGeneral, Reglamento, ReglamentoEtapa
│   ├── core.py                 # Geografía y temas (Region, Commune, Topic…)
│   ├── legislature.py          # Partido, Legislador, Cámara, Comisión…
│   ├── proyecto.py             # Bill, BillStage, BillDocument…
│   ├── votacion.py             # VotingSession, Vote, LegislatorVotingStats
│   └── sync.py                 # ClientSyncState, ChangeLog
├── schemas/                    # Esquemas Pydantic de entrada/salida
└── services/                   # Lógica de consulta por dominio
migrations/
└── versions/
    └── bbee1706b4c2_initial_schema.py   # Migración inicial (todas las tablas)
```

## Ejecución local

```bash
# Variables de entorno
cp .env .env.local
# Editar .env.local con DATABASE_URL local, ej:
# DATABASE_URL=postgresql://gpd@localhost:5432/diariooficial_dev

# Instalar dependencias
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Migraciones
alembic upgrade head

# Servidor de desarrollo
uvicorn app.main:app --reload
```

Documentación interactiva disponible en [http://localhost:8000/docs](http://localhost:8000/docs).

## Clonar la base de datos remota en local

```bash
# Clona la BD de Railway en diariooficial_dev (requiere Docker)
bash scripts/clone_db_to_local.sh

# Nombre personalizado
bash scripts/clone_db_to_local.sh mi_bd_local
```

## Migraciones

```bash
# Aplicar todas las migraciones pendientes
alembic upgrade head

# Generar nueva migración a partir de cambios en los modelos
alembic revision --autogenerate -m "descripcion"

# Ver el estado actual
alembic current
```

## Docker

```bash
docker build -t diariooficial-api .
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://usuario:password@host:5432/diariooficial \
  diariooficial-api
```

## Endpoints disponibles

### Diario Oficial

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/v1/diario-oficial/normas` | Lista normas con filtros y paginación |
| `GET` | `/api/v1/diario-oficial/normas/{cve}` | Norma por CVE |
| `GET` | `/api/v1/diario-oficial/normas/por-importancia` | Normas destacadas |
| `GET` | `/api/v1/diario-oficial/dates/available` | Fechas con publicaciones |
| `GET` | `/api/v1/diario-oficial/stats/by-ministry` | Conteo por ministerio |

Parámetros de filtro para `/normas`: `date_from`, `date_to`, `ministry`, `branch`, `search`, `offset`, `limit` (máx 500).

### Reglamentos CGR

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/v1/reglamentos` | Lista reglamentos con filtros |
| `GET` | `/api/v1/reglamentos/{id}` | Detalle de un reglamento |
| `GET` | `/api/v1/reglamentos/recientes` | Último estado de reglamentos activos |
| `GET` | `/api/v1/reglamentos/stats/por-ministerio` | Conteo por ministerio |
| `GET` | `/api/v1/reglamentos/stats/por-categoria` | Conteo por categoría |
| `GET` | `/api/v1/reglamentos/stats/tiempo-tramitacion` | Tiempo promedio de tramitación |
| `GET` | `/api/v1/reglamentos/stats/mas-etapas` | Reglamentos con más etapas |

### Proyectos de ley y Sync

En desarrollo — retornan HTTP 501 temporalmente.

## Variables de entorno

| Variable | Requerida | Descripción |
|----------|-----------|-------------|
| `DATABASE_URL` | ✅ | URL de conexión PostgreSQL |
