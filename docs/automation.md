# Automatización de la ingesta (GitHub Actions, costo $0)

## Qué corre y cuándo

Un solo workflow `.github/workflows/ingestion.yml`:

- **Cron diario `0 5 * * *` (UTC).** El job `decide` elige modo:
  - Lunes → `full` (matriz completa, 32 términos × 8 años = 256 queries).
  - Martes–domingo → `daily` (misma matriz: `recent_years: 8`).
  El modo ya no cambia el alcance; el **cursor** decide qué falta.
- **`workflow_dispatch`** para corrida manual con inputs: `mode`,
  `min_popularity`, `limit_queries`, `request_budget`.

Un **solo job**, siempre. Dos shards en paralelo (~700 requests en minutos)
dispararon el baneo de 24 h del 2026-11-16.

Duración: ~400 requests × 2 s ≈ 15 min + escrituras. Consumo mensual
~450 min, dentro de los 2.000 free (privado) o ilimitado (público).

## Cómo crece el dataset (importante)

Cada query se pagina de 10 en 10 (límite de la app), con offsets 0–990:
hasta **1.000 tracks por query**. El estado vive en la colección
`query_cursors`:

| Campo | Significado |
|---|---|
| `next_offset` | Primera página que **no** se ha pedido todavía |
| `exhausted` | La query devolvió una página corta: no hay más resultados |
| `last_run_at` | Rotación justa: primero las queries tocadas hace más tiempo |

Consecuencia: una corrida **nunca relee lo ya capturado**. Antes el daily
gastaba 292 requests en las mismas 96 queries desde offset 0 y sumaba
~7 tracks nuevos; ahora el mismo presupuesto avanza terreno inédito
(~85–95 % de novedad medido en offsets 500–990).

Crecimiento medido: **6–7 tracks nuevos por request**; con
`request_budget: 400` son **~2.500–2.800/día**. Los 100.000 se alcanzan en
**~4 semanas** sin tocar código: el motor solo consume la matriz
(256 queries × hasta 1.000 tracks = 256k de techo bruto).

## Presupuesto y cuota

- `request_budget` (settings o `--request-budget` / env `REQUEST_BUDGET`):
  techo de requests por corrida. Se corta limpio con `stop_reason:
  budget_reached` (status verde).
- `sleep_seconds: 2` entre páginas.
- Techo observado de la app en Development Mode: ~600 requests/día; al
  pasarlo responde 429 con `Retry-After` de ~24 h. Subir el presupuesto
  de 400 → 500 solo si `http_429_count` se mantiene en 0 varios días.
- Telemetría por corrida en `ingestion_runs`: `total_requests`,
  `http_429_count`, `retry_after_max_seconds`, `stop_reason`,
  `total_inserted_new`, `total_matched_existing`, `queries_exhausted_now`.
- Circuit breaker: cada corrida sondea con 1 request; si la cuota sigue
  quemada registra `skipped_quota` y termina en verde sin quemar cientos
  de requests.

## Cuándo ampliar la matriz

El motor agota queries: cuando `queries_exhausted_now` crece y
`total_inserted_new` cae por debajo de ~500/corrida de forma sostenida,
toca añadir términos a `spotify.query_terms` (cada término nuevo aporta
8 queries × hasta 1.000 tracks). Ver `docs/runbook.md`.

## Aviso cuenta Free + repo privado

En cuentas Free, `schedule` **no dispara en repositorios privados** (solo público o Pro).
Opciones:

1. Repo **público** (recomendado: es proyecto de portafolio).
2. Servicio externo (p. ej. cron-job.org) que llame `POST /repos/<owner>/<repo>/actions/workflows/ingestion.yml/dispatches`
   con un PAT y `{"ref":"main","inputs":{"mode":"daily"}}`.

## Secretos (Settings → Secrets → Actions)

| Secreto | Origen |
|---|---|
| `SPOTIFY_CLIENT_ID` | Spotify Dashboard |
| `SPOTIFY_CLIENT_SECRET` | Spotify Dashboard |
| `MONGO_URI` | Atlas M0 (`mongodb+srv://...`) |
| `MONGO_DB_NAME` | `spotify_electronica_colombia` |

Rotación: regenerar en Spotify/Atlas y actualizar aquí. Nunca en código.

## Smoke test antes de un cambio grande

Actions → Ingestion pipeline → Run workflow → `limit_queries: 2`,
`request_budget: 20`, `mode: daily`.
Verifica en logs: `Resumen: requests=../20 ... nuevos=... | 429=0`.
