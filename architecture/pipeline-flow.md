# Flujo del pipeline ELT

> Fuente viva de este diagrama. El archivo anterior `pipeline-flow.mmd`
> (texto plano, GitHub no lo renderizaba) fue reemplazado por este `.md`.

```mermaid
flowchart TD
    A[Cron GHA diario 05:00 UTC<br/>o workflow_dispatch manual] --> B{Es lunes?}
    B -- Si --> C[Mode FULL<br/>32 terminos x 2018-2025 = 256 queries]
    B -- No --> D[Mode DAILY<br/>misma matriz, recent_years=8]

    C --> E[Cargar settings.yaml + secretos]
    D --> E

    E --> F[Sembrar/leer cursor por query<br/>coleccion query_cursors]
    F --> G[Token OAuth 2.0 cacheado 1h]
    G --> H[Por query: paginar search<br/>limit=10, offset hasta 990]

    H --> I{429 con<br/>Retry-After mayor 300s?}
    I -- Si --> J[Abortar corrida<br/>status=quota_exhausted<br/>cursor guardado]
    I -- No --> K[429/5xx: backoff + reintento]

    K --> L[Transform: aplanar track JSON<br/>filtro min_popularity si aplica]
    L --> M[Bulk upsert en curated_tracks<br/>indice unico spotify_track_id]

    M --> N{Presupuesto de<br/>requests agotado?}
    N -- Si --> O[Cortar limpio<br/>stop_reason=budget_reached]
    N -- No --> P{2 paginas vacias<br/>seguidas?}
    P -- Si --> Q[Marcar query exhausted<br/>siguiente query]
    P -- No --> R[Avanzar cursor + sleep]
    R --> H

    O --> S
    Q --> S[Doc en ingestion_runs:<br/>requests, 429s, nuevos vs existentes]
    S --> T[build_analysis.py -> analysis_tracks]
    T --> U[Dashboard Streamlit publico]
```

## Cómo crece el dataset

- Cada query tiene un cursor (`next_offset`) que apunta a la primera página
  **no pedida**. Una corrida nunca relee terreno conocido.
- El pipeline anterior se detenía en el offset 490: toda query ya ejecutada
  reanuda en 500, donde la novedad medida es de 85-100%.
- Presupuesto: `request_budget` (400) requests por corrida, un solo job.
  Rendimiento medido: ~6-7 tracks nuevos por request.

## Reglas verificadas contra la API (2026-09)

- `limit=10` máximo aceptado (`>10` da `400 Invalid limit`).
- `offset` máximo útil 990 (`offset + limit <= 1000`).
- Una página corta **no** significa fin de resultados: Spotify devuelve
  páginas incompletas a mitad de camino. Solo se marca `exhausted` tras 2
  páginas vacías consecutivas.
- `popularity` llega nulo en search y `/v1/tracks` da `403`: el filtro por
  umbral está implementado pero inactivo (`min_popularity: 0`).
  Detalle en `docs/data-source.md` §19.
- Endpoints bloqueados (403) en Development Mode: `/v1/tracks?ids=`,
  `/v1/albums?ids=`, `/v1/artists?ids=`, `/v1/playlists/{id}/tracks`.
  Sí funcionan: `/v1/search`, `/v1/albums/{id}`, `/v1/albums/{id}/tracks`
  (`limit=50`).
