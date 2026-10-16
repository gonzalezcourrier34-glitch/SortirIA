"""Mesure du coût et du temps de réponse de la recherche (16/10/2026).

**Provenance** : nouveau script écrit pour SortirIA.

    uv run python -m scripts.mesure_cout_latence                      # 100 questions, sans cache
    uv run python -m scripts.mesure_cout_latence --n 1000 --oui       # 1 000 questions (~0,5 $)
    uv run python -m scripts.mesure_cout_latence --cache              # avec le cache des réponses
    uv run python -m scripts.mesure_cout_latence --modele ministral-8b-latest   # autre modèle de rédaction
    uv run python -m scripts.mesure_cout_latence --sans-redaction     # recherche seule, sans Mistral

Les questions viennent des deux jeux d'évaluation (`config/eval_recherche.yaml`
et `config/eval_mots.yaml`, 175 questions), tirées dans un ordre fixe et
répétées jusqu'à `--n`. Chaque question passe par la vraie recherche
(`run_search`) sur la base du `.env`, **sans être tracée** dans
`rag_requests` (elle ne compterait pas parmi les vraies demandes).

Mesures :

- temps de réponse de bout en bout : médiane, 95e centile (P95), maximum ;
- temps de chaque étape : comprendre, rechercher (embedding de la question et
  requêtes SQL), filtrer, rédiger (Mistral) ;
- coût : rédaction (tokens rendus par Mistral, grille de `agent/watch.py`)
  et embedding de la question (estimé : l'API ne rend pas le nombre de
  tokens) ; ramené à 1 000 questions ;
- avec `--cache` : deux passages des mêmes questions, le second servi par le
  cache ; le rapport donne les deux, et le coût pour une part de questions
  répétées donnée.

Le temps mesuré depuis un poste de travail inclut l'aller-retour vers la base
(Azure, si le `.env` y pointe) ; en production, l'API est à côté de la base.
Le détail par étape montre ce qui vient de la base et ce qui vient de Mistral.

Chaque lancement est gardé dans `benchmarks/cout_latence/` (rapport JSON et
ligne dans `historique.csv`).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.search_service import (
    PARIS,
    PROMPT_VERSION,
    STEP_FILTER,
    STEP_SEARCH,
    STEP_UNDERSTAND,
    STEP_WRITE,
    SearchFilters,
    SearchServices,
    embed_question,
    request_cost,
    run_search,
    write_with_mistral,
)

ROOT = Path(__file__).resolve().parents[1]
QUESTION_FILES = (ROOT / "config" / "eval_recherche.yaml", ROOT / "config" / "eval_mots.yaml")
BENCH_DIR = ROOT / "benchmarks" / "cout_latence"
STEPS = (STEP_UNDERSTAND, STEP_SEARCH, STEP_FILTER, STEP_WRITE)
COST_PER_QUESTION_GUESS = 0.0006          # pour l'annonce avant de lancer (rédaction + embedding)
CONFIRM_ABOVE = 100


def load_questions(files: Sequence[Path] = QUESTION_FILES) -> list[dict[str, Any]]:
    """Questions et filtres des jeux d'évaluation."""

    items = []
    for path in files:
        for item in yaml.safe_load(path.read_text(encoding="utf-8"))["questions"]:
            items.append({"question": str(item["question"]), "filtres": item.get("filtres") or {}})
    return items


def pick(items: Sequence[Mapping[str, Any]], n: int, seed: int = 16) -> list[Mapping[str, Any]]:
    """`n` questions dans un ordre fixe (même tirage d'un lancement à l'autre)."""

    order = list(items)
    random.Random(seed).shuffle(order)
    return [order[index % len(order)] for index in range(n)]


def percentile(values: Sequence[float], share: float) -> float:
    """Centile par interpolation (comme percentile_cont de PostgreSQL)."""

    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * share
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def embedding_cost(question: str) -> float:
    """Coût estimé de l'embedding de la question (4 caractères par token environ)."""

    from agent.watch import estimate_tokens

    return request_cost("mistral-embed", {"input": estimate_tokens([question]), "output": 0})


def measure_one(connection: Any, item: Mapping[str, Any], services: SearchServices,
                models: list[str], clock: Any = time.perf_counter) -> dict[str, Any]:
    """Une recherche : temps total et par étape, coût, cache ou non."""

    models.clear()
    started = clock()
    done: dict[str, float] = {}
    result: dict[str, Any] = {}
    from_cache = False
    for event in run_search(connection, str(item["question"]), SearchFilters(**item["filtres"]), services):
        if event.get("event") == "progress" and event.get("status") == "completed":
            done.setdefault(event["step"], clock())
            from_cache = from_cache or "cache" in str(event.get("message", ""))
        elif event.get("event") == "completed":
            result = event["result"]
    total = clock() - started
    steps, previous = {}, started
    for step in STEPS:
        if step in done:
            steps[step] = round((done[step] - previous) * 1000, 1)
            previous = done[step]
    tokens = result.get("tokens") or {}
    write_cost = request_cost(models[0], tokens) if models and tokens else 0.0
    embed_cost = 0.0 if from_cache or services.embed is None else embedding_cost(str(item["question"]))
    return {"question": item["question"], "ms": round(total * 1000, 1), "etapes_ms": steps,
            "cache": from_cache, "cartes": len(result.get("events") or []), "degradee": bool(result.get("degraded")),
            "tokens": tokens, "modele": models[0] if models else "", "cout_usd": round(write_cost + embed_cost, 7)}


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Médiane, P95 et maximum ; temps moyen par étape ; coût moyen et pour 1 000."""

    if not rows:
        return {"questions": 0}
    times = [float(row["ms"]) for row in rows]
    costs = [float(row["cout_usd"]) for row in rows]
    steps = {step: round(statistics.mean(float(row["etapes_ms"].get(step, 0.0)) for row in rows), 1)
             for step in STEPS}
    return {
        "questions": len(rows),
        "mediane_ms": round(statistics.median(times), 1),
        "p95_ms": round(percentile(times, 0.95), 1),
        "max_ms": round(max(times), 1),
        "etapes_moyennes_ms": steps,
        "cout_moyen_usd": round(statistics.mean(costs), 7),
        "cout_1000_usd": round(statistics.mean(costs) * 1000, 4),
        "part_cache": round(sum(1 for row in rows if row["cache"]) / len(rows), 3),
        "degradees": sum(1 for row in rows if row["degradee"]),
        "sans_carte": sum(1 for row in rows if not row["cartes"]),
    }


def mixed_cost(miss: Mapping[str, Any], hit: Mapping[str, Any], repeated: float) -> float:
    """Coût pour 1 000 questions quand une part `repeated` est servie par le cache."""

    return round((1 - repeated) * float(miss.get("cout_1000_usd", 0)) + repeated * float(hit.get("cout_1000_usd", 0)), 4)


def run(n: int, *, cache: bool, write: bool, model: str = "") -> dict[str, Any]:
    """Lance la mesure et rend le rapport."""

    from agent.services import connect_postgres

    if model:
        os.environ["SEARCH_MODEL"] = model
    models: list[str] = []

    def writer(question: str, filters: Any, cards: Any) -> tuple[str, dict[str, int], str]:
        raw, usage, used = write_with_mistral(question, filters, cards)
        models.append(used)
        return raw, usage, used

    services = SearchServices(embed=embed_question, write=writer if write else None, log=False, cache=cache)
    questions = pick(load_questions(), n)
    passes: dict[str, list[dict[str, Any]]] = {"premier_passage": []}
    with connect_postgres() as connection:
        for index, item in enumerate(questions, start=1):
            passes["premier_passage"].append(measure_one(connection, item, services, models))
            if index % 25 == 0:
                print(f"  {index}/{n} questions…", flush=True)
        if cache:
            passes["second_passage"] = [measure_one(connection, item, services, models) for item in questions]
    report: dict[str, Any] = {
        "date": datetime.now(PARIS).isoformat(timespec="seconds"),
        "questions": n, "cache": cache, "redaction": write,
        "modele": (model or os.getenv("SEARCH_MODEL", "mistral-small-latest")) if write else "",
        "prompt_version": PROMPT_VERSION,
        "resultats": {name: summarize(rows) for name, rows in passes.items()},
        "detail": passes,
    }
    if cache:
        miss, hit = report["resultats"]["premier_passage"], report["resultats"]["second_passage"]
        report["cout_1000_selon_repetition"] = {f"{int(share * 100)} %": mixed_cost(miss, hit, share)
                                               for share in (0.0, 0.3, 0.5, 0.7)}
    return report


def save(report: Mapping[str, Any], note: str = "") -> Path:
    """Rapport complet en JSON et une ligne dans l'historique."""

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(PARIS).strftime("%Y-%m-%d_%Hh%M")
    path = BENCH_DIR / f"{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    first = report["resultats"]["premier_passage"]
    second = report["resultats"].get("second_passage", {})
    history = BENCH_DIR / "historique.csv"
    new = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["date", "questions", "cache", "redaction", "modele", "prompt_version",
                             "mediane_ms", "p95_ms", "cout_1000_usd", "p95_ms_cache", "cout_1000_usd_cache", "note"])
        writer.writerow([report["date"], report["questions"], report["cache"], report["redaction"], report["modele"],
                         report["prompt_version"], first.get("mediane_ms"), first.get("p95_ms"),
                         first.get("cout_1000_usd"), second.get("p95_ms", ""), second.get("cout_1000_usd", ""), note])
    return path


def print_report(report: Mapping[str, Any]) -> None:
    for name, summary in report["resultats"].items():
        print(f"\n{name.replace('_', ' ').capitalize()} : {summary['questions']} questions")
        print(f"  temps : médiane {summary['mediane_ms']:.0f} ms, P95 {summary['p95_ms']:.0f} ms, "
              f"max {summary['max_ms']:.0f} ms")
        print("  par étape (moyenne) : " + ", ".join(f"{step} {ms:.0f} ms"
                                                    for step, ms in summary["etapes_moyennes_ms"].items()))
        print(f"  coût : {summary['cout_moyen_usd']:.6f} $ par question, {summary['cout_1000_usd']:.3f} $ "
              f"pour 1 000 ; servies par le cache : {summary['part_cache']:.0%} ; "
              f"dégradées : {summary['degradees']} ; sans carte : {summary['sans_carte']}")
    for share, cost in (report.get("cout_1000_selon_repetition") or {}).items():
        print(f"  1 000 questions dont {share} déjà posées le même jour : {cost:.3f} $")


def main() -> None:
    from config.environment import load_environment

    parser = argparse.ArgumentParser(description="Mesure du coût et du temps de réponse de la recherche.")
    parser.add_argument("--n", type=int, default=100, help="Nombre de questions (défaut : 100).")
    parser.add_argument("--cache", action="store_true", help="Avec le cache : deux passages des mêmes questions.")
    parser.add_argument("--sans-redaction", action="store_true", help="Recherche seule, sans rédaction par Mistral.")
    parser.add_argument("--modele", default="", help="Modèle de rédaction (ex. ministral-8b-latest).")
    parser.add_argument("--note", default="", help="Note gardée dans l'historique.")
    parser.add_argument("--oui", action="store_true", help=f"Lance sans confirmer au-delà de {CONFIRM_ABOVE} questions.")
    args = parser.parse_args()

    load_environment()
    estimate = 0.0 if args.sans_redaction else args.n * COST_PER_QUESTION_GUESS
    print(f"{args.n} questions, coût Mistral estimé : environ {estimate:.2f} $ (non tracées dans rag_requests).")
    if args.n > CONFIRM_ABOVE and not args.oui:
        print(f"Au-delà de {CONFIRM_ABOVE} questions, ajoutez --oui pour confirmer.")
        return
    report = run(args.n, cache=args.cache, write=not args.sans_redaction, model=args.modele)
    print_report(report)
    print(f"\nRapport gardé : {save(report, args.note).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
