"""Reporte de crecimiento del data lake.

Uso:
    python scripts/growth_report.py            # últimas 14 corridas
    python scripts/growth_report.py --runs 40

Responde tres preguntas de operación:
  1. ¿Cuánto creció el dataset y a qué ritmo?
  2. ¿Cuánto presupuesto de requests se está usando y con qué rendimiento?
  3. ¿Cuánto terreno queda en la matriz de queries?
"""
import argparse
import sys
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.mongo import close_mongo_client, get_collection


TARGET = 100_000


def main() -> None:
    parser = argparse.ArgumentParser(description="Reporte de crecimiento")
    parser.add_argument("--runs", type=int, default=14)
    args = parser.parse_args()

    tracks = get_collection("curated_tracks")
    total = tracks.count_documents({})
    print(f"Tracks en curated_tracks : {total:,}")
    print(f"Faltan para {TARGET:,}    : {max(0, TARGET - total):,}")

    runs = list(
        get_collection("ingestion_runs")
        .find(
            {"total_inserted_new": {"$exists": True}},
            {
                "run_started_at": 1, "mode": 1, "status": 1, "stop_reason": 1,
                "total_requests": 1, "total_inserted_new": 1,
                "total_matched_existing": 1, "http_429_count": 1,
                "request_budget": 1,
            },
        )
        .sort("run_started_at", -1)
        .limit(args.runs)
    )

    if not runs:
        print("\nAún no hay corridas con la métrica nueva (total_inserted_new).")
    else:
        print(f"\nÚltimas {len(runs)} corridas:")
        print(f"{'fecha':17} {'modo':6} {'req':>4} {'nuevos':>7} {'existentes':>10} "
              f"{'nuevos/req':>10} {'429':>4} {'stop':>18}")
        for run in runs:
            req = run.get("total_requests") or 0
            new = run.get("total_inserted_new") or 0
            ratio = (new / req) if req else 0
            print(
                f"{str(run.get('run_started_at'))[:16]:17} "
                f"{str(run.get('mode')):6} {req:>4} {new:>7} "
                f"{run.get('total_matched_existing') or 0:>10} "
                f"{ratio:>10.2f} {run.get('http_429_count') or 0:>4} "
                f"{str(run.get('stop_reason') or '-'):>18}"
            )

        # Proyección con el rendimiento de la corrida más reciente del motor
        # nuevo (las que traen request_budget), no con el histórico viejo.
        reference = next((r for r in runs if r.get("request_budget")), runs[0])
        req = reference.get("total_requests") or 0
        ratio = ((reference.get("total_inserted_new") or 0) / req) if req else 0
        budget = reference.get("request_budget") or 400
        per_run = ratio * budget
        print(f"\nRendimiento de referencia        : {ratio:.2f} nuevos/request "
              f"× {budget} requests = {per_run:,.0f} nuevos/corrida")
        if per_run > 0:
            remaining = max(0, TARGET - total)
            runs_needed = remaining / per_run
            print(f"Proyección a {TARGET:,}            : {runs_needed:.0f} corridas "
                  f"(~{runs_needed / 7:.1f} semanas)")

    cursors = list(get_collection("query_cursors").find({}, {
        "query": 1, "next_offset": 1, "exhausted": 1,
    }))
    if cursors:
        exhausted = [c for c in cursors if c.get("exhausted")]
        pending = [c for c in cursors if not c.get("exhausted")]
        remaining_slots = sum(
            max(0, 1000 - int(c.get("next_offset") or 0)) for c in pending
        )
        print(f"\nCursors: {len(cursors)} queries | "
              f"pendientes {len(pending)} | agotadas {len(exhausted)}")
        print(f"Terreno restante en la matriz : ~{remaining_slots:,} tracks brutos")
        print(f"Actualizado: {datetime.now(UTC).isoformat()[:16]} UTC")

    close_mongo_client()


if __name__ == "__main__":
    main()
