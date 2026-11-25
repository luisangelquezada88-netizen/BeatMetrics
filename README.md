# BeatMetrics

[![CI](https://github.com/luisangelquezada88-netizen/spotify-electronica-colombia/actions/workflows/ci.yml/badge.svg)](https://github.com/luisangelquezada88-netizen/spotify-electronica-colombia/actions/workflows/ci.yml)
[![Ingestion](https://github.com/luisangelquezada88-netizen/spotify-electronica-colombia/actions/workflows/ingestion.yml/badge.svg)](https://github.com/luisangelquezada88-netizen/spotify-electronica-colombia/actions/workflows/ingestion.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-Luis%20Angel%20Quezada-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/luis-angel-quezada-aguirre-291333268)

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Spotify Web API](https://img.shields.io/badge/Spotify-Web%20API-1DB954?logo=spotify&logoColor=white)](https://developer.spotify.com/documentation/web-api)
[![MongoDB Atlas](https://img.shields.io/badge/MongoDB-Atlas%20M0-47A248?logo=mongodb&logoColor=white)](https://www.mongodb.com/atlas)
[![Streamlit](https://img.shields.io/badge/Streamlit-App-FF4B4B?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Plotly](https://img.shields.io/badge/Plotly-6.9-3F4F75?logo=plotly&logoColor=white)](https://plotly.com/python/)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-CI%2FCD-2088FF?logo=githubactions&logoColor=white)](https://github.com/features/actions)

**Live demo:** https://spotify-electronica-colombia.streamlit.app

*Nota*: Si la app se encuentra dormida por inactividad, presiona `Yes, get this app back up!`

**BeatMetrics** es un pipeline ELT end-to-end para recolectar, almacenar,
transformar y visualizar metadatos de música electrónica asociada al mercado
colombiano, usando la Spotify Web API como fuente y MongoDB como capa de
aterrizaje. Automatizado con GitHub Actions y desplegado a **costo cero**
(MongoDB Atlas M0 + Streamlit Community Cloud).

## Arquitectura de datos

```mermaid
flowchart LR
    A[Spotify Web API<br/>Search, market=CO] --> B[Ingesta Python<br/>token cache + retry + backoff]
    B --> C[(MongoDB Atlas M0<br/>curated_tracks)]
    B --> D[(ingestion_runs<br/>metadata + stats)]
    C --> E[build_analysis.py]
    E --> F[(analysis_tracks)]
    F --> G[Dashboard Streamlit<br/>URL pública]

    subgraph Automatización - GitHub Actions
        H[Cron diario 05:00 UTC<br/>1 job + cursor por query<br/>presupuesto 400 req]
    end
    H -. dispara .-> B
```

Flujo detallado en [`architecture/pipeline-flow.md`](architecture/pipeline-flow.md)
y diagrama general en [`architecture/general-architecture.mmd`](architecture/general-architecture.mmd).
Decisiones de diseño: [`docs/architecture.md`](docs/architecture.md) ·
fuente: [`docs/data-source.md`](docs/data-source.md) ·
metodología: [`docs/methodology.md`](docs/methodology.md).

Capas en MongoDB:

| Colección | Contenido |
|---|---|
| `curated_tracks` | Tracks aplanados y deduplicados (índice único `spotify_track_id`) |
| `analysis_tracks` | Dataset derivado para el dashboard (año, duración, contadores) |
| `query_cursors` | Cursor por query: próximo offset a pedir y si ya se agotó |
| `ingestion_runs` | Metadata por corrida: modo, requests, `http_429_count`, nuevos vs existentes, errores |

## Opción A — Ver el dashboard (sin instalar nada)

Abre https://spotify-electronica-colombia.streamlit.app en cualquier navegador.
No requiere login. Si la app estuvo inactiva varios días, la primera carga
tarda 30–60 s (política del tier gratuito).

## Opción B — Correr el dashboard en local

```powershell
pip install -r requirements.txt
# .env con MONGO_URI y MONGO_DB_NAME (ver .env.example)
streamlit run src/dashboard_app.py
```

## Opción C — Pipeline completo con Docker

```powershell
cp .env.example .env   # completa SPOTIFY_CLIENT_ID/SECRET y MONGO_URI
docker build -t spotify-etl .
# Smoke test: 2 queries recientes
docker run --rm --env-file .env spotify-etl python run_ingestion.py --mode daily --limit-queries 2
# Corrida diaria real + rebuild analítico
docker run --rm --env-file .env spotify-etl python run_ingestion.py --mode daily
docker run --rm --env-file .env spotify-etl python scripts/build_analysis.py
```

## CLI de ingesta

```powershell
python run_ingestion.py --mode daily                    # refresh años recientes
python run_ingestion.py --mode full                     # matriz completa
python run_ingestion.py --mode full --min-popularity 20 # con umbral de popularidad
python run_ingestion.py --mode daily --limit-queries 2  # smoke test
python scripts/backfill_popularity.py --dry-run         # hidratar popularity faltante
python scripts/inspect_curated.py                       # ver un documento de ejemplo
```

## Automatización y despliegue

- **Scheduling:** GitHub Actions, cron diario 05:00 UTC (un solo job con
  cursor persistente por query: nunca relee lo ya capturado) + disparo manual.
  Detalle: [`docs/automation.md`](docs/automation.md).
- **Crecimiento:** cada corrida gasta su presupuesto de requests en
  páginas que nunca se pidieron (cursor por query), no en refrescar lo ya
  capturado. Medido: ~6–7 tracks nuevos por request.
- **Base de datos:** MongoDB Atlas M0 (tier gratuito permanente).
- **Dashboard:** Streamlit Community Cloud (URL pública).
  Paso a paso: [`docs/deployment.md`](docs/deployment.md).
- **Operación:** [`docs/runbook.md`](docs/runbook.md) (429, 401, backfill, rollback).

Secretos requeridos (`Settings → Secrets → Actions` y Secrets de Streamlit):

| Secreto | Origen |
|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) |
| `MONGO_URI` / `MONGO_DB_NAME` | MongoDB Atlas (cluster M0) |

## Límites conocidos

- La app de Spotify rechaza `limit > 10` en search (`400 Invalid limit`,
  verificado 2026-09-15): `config/settings.yaml` fija `limit: 10`.
- Spotify aplica rate limits agresivos en Development Mode (~600 requests/día;
  al pasarlo responde `429` con `Retry-After` de hasta 24 h). Por eso corre un
  solo job con presupuesto configurable (`request_budget`), backoff topado y
  aborto con progreso parcial (`status: quota_exhausted`).
- `popularity` puede llegar nulo desde la API para parte del catálogo;
  se hidrata con `scripts/backfill_popularity.py`.

## Estructura

```text
├── .github/workflows/      # ingestion.yml (diaria + semanal) y ci.yml
├── architecture/           # diagramas .mmd del pipeline
├── config/settings.yaml    # queries, paginación, min_popularity, recent_years
├── docs/                   # arquitectura, fuente, metodología, automation, deployment, runbook
│   └── images/pipeline.png # diagrama exportado del pipeline
├── scripts/                # build_analysis, backfill_popularity, inspect_curated
├── src/                    # auth, search, ingestion, transform, mongo, dashboard_app
├── tests/diagnostics/      # checks manuales de conexión, conteos y agregaciones
├── Dockerfile
├── run_ingestion.py        # entrypoint CLI
└── LICENSE                 # MIT
```

## Licencia

Distribuido bajo licencia **MIT**. Ver [`LICENSE`](LICENSE).

Los metadatos musicales provienen de la Spotify Web API y siguen sujetos a
los [términos de uso de Spotify](https://developer.spotify.com/terms); este
repositorio solo distribuye el código de la infraestructura.
