"""Évaluation de la recherche SortirIA sur un jeu de questions.

**Provenance** : nouveau script écrit pour SortirIA (15/10/2026).

    uv run python -m scripts.eval_recherche                    # recherche seule (embeddings : ~0,0001 $)
    uv run python -m scripts.eval_recherche --avec-redaction   # + réponse de Mistral (~0,01 $)
    uv run python -m scripts.eval_recherche --note "recherche hybride"   # note gardée dans l'historique
    uv run python -m scripts.eval_recherche --jeu-test --seuil 0.95       # en CI (16/10/2026)

En CI (`.github/workflows/ci.yml`, job « Évaluation de la recherche ») : la
base de test est chargée depuis `tests/data/jeu_recherche.json.gz`
(`scripts/jeu_test_recherche.py`), l'horloge est placée à l'instant de
l'export (celle de la recherche et celle de la base, `sortiria.maintenant`,
lue par la vue `v_upcoming_events`), la recherche se fait sans vecteur ni
Mistral, rien n'est gardé dans `benchmarks/`, et le job échoue si la précision
ou la compréhension passe sous le seuil (`below_threshold`).

Pour chaque question de `config/eval_recherche.yaml`, la vraie recherche est
lancée sur la vraie base (sans être tracée dans `rag_requests`). Trois mesures :

1. **Compréhension** : la question a-t-elle été comprise comme attendu
   (période, lieu, public, gratuit, soir, type) ?
2. **Précision des propositions** : part des événements proposés qui
   respectent les contraintes attendues (date, lieu, public, gratuit, type,
   mots attendus). Mesure automatique, sans jugement humain : elle dit si la
   recherche se trompe de cible, pas si les propositions sont les meilleures.
   Quand la base n'a rien d'exact (« du théâtre gratuit en famille »), la
   recherche propose des idées voisines et la réponse le dit : ces questions
   sont comptées à part (`idees_voisines`), pas dans la précision.
3. **Temps de réponse** (médiane et 95e centile) et **questions sans résultat**.

L'objectif de la feuille de route est 95 % de propositions correctes.

**Chaque lancement est gardé** (`benchmarks/recherche/`) :
- `historique.csv` : une ligne par lancement (date, mode, jeu de questions,
  version du prompt, mesures, note) : l'évolution d'un coup d'œil ;
- `AAAA-MM-JJ_HHhMM.json` : le rapport complet (question par question) ;
- MLflow (expérience `sortiria-evaluation-recherche`) quand le serveur répond :
  mêmes mesures, comparables en graphique, rapport en pièce jointe.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import statistics
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from app.query_understanding import MOMENT_HOURS, QUESTION_CATEGORIES, QueryPlan, plain
from app.search_answer import PROMPT_VERSION, write_with_mistral
from app.search_filters import PARIS, SearchFilters
from app.search_query import embed_question
from app.search_service import SearchServices, plan_search, run_search
from config.environment import load_environment

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_FILE = ROOT / "config" / "eval_recherche.yaml"
BENCH_DIR = ROOT / "benchmarks" / "recherche"
HISTORY_COLUMNS = ("date", "mode", "jeu", "questions", "prompt_version", "comprehension", "precision",
                   "idees_voisines", "sans_resultat", "latence_mediane_s", "latence_p95_s", "note")
MLFLOW_EXPERIMENT = "sortiria-evaluation-recherche"
OUTDOOR = {"nature", "marche", "sport", "fete", "festival"}
# Titre qui dit qu'on participe (17/10/2026) : « Stage de chant », rangé en
# concert, reste une réponse à « je veux chanter ».
PARTICIPATION = re.compile(r"\b(cours|stage|atelier|initiation|karaoke|scene ouverte|bal|guinguette|tournoi)\b")
# Ce qui se pratique, pour « regarder » (« un spectacle de danse » : pas un bal).
PRACTISED = re.compile(rf"{PARTICIPATION.pattern}|\bdansant|\bbaleti|\bmilonga")
DAY_LABEL = re.compile(r"^(lun|mar|mer|jeu|ven|sam|dim)\. \d+ ")
TARGET = 0.95


# ----------------------------------------------------------------------------
# Compréhension
# ----------------------------------------------------------------------------

def understanding_errors(plan: QueryPlan, expected: Mapping[str, Any]) -> list[str]:
    """Écarts entre ce qui a été compris et ce qui était attendu.

    Seules les clés présentes dans `expected` sont contrôlées ; « quand: jour »
    accepte n'importe quel jour précis (« sam. 24 oct. »).

    Parameters
    ----------
    plan : QueryPlan
        Le plan de recherche tiré de la question.
    expected : Mapping[str, Any]
        Ce qui doit être compris (section `compris` du YAML : quand, communes,
        zone, public, gratuit, soir, types, budget, cadre, pres_de, pratique,
        moment, regarder).

    Returns
    -------
    list[str]
        Un message par écart ; vide si tout a été compris.
    """

    errors = []
    # Période
    when = expected.get("quand")
    if when == "jour" and not DAY_LABEL.match(plan.period_label + " "):
        errors.append(f"période « {plan.period_label} » au lieu d'un jour précis")
    elif when and when != "jour" and plan.period_label != when:
        errors.append(f"période « {plan.period_label} » au lieu de « {when} »")
    # Lieu et public
    if "communes" in expected and set(plan.cities) != set(expected["communes"]):
        errors.append(f"communes {list(plan.cities)} au lieu de {expected['communes']}")
    if "zone" in expected and plan.zone != expected["zone"]:
        errors.append(f"zone {plan.zone} au lieu de {expected['zone']}")
    if "public" in expected and plan.public != expected["public"]:
        errors.append(f"public {plan.public} au lieu de {expected['public']}")
    # Gratuité, soirée, type de sortie, budget et cadre
    if expected.get("gratuit") and not plan.free_only:
        errors.append("gratuit non compris")
    if expected.get("soir") and not plan.evening:
        errors.append("soir non compris")
    if "types" in expected and not set(plan.categories) & set(expected["types"]):
        errors.append(f"type {list(plan.categories)} au lieu de {expected['types']}")
    if "budget" in expected and plan.max_price != float(expected["budget"]):
        errors.append(f"budget {plan.max_price} au lieu de {expected['budget']}")
    if "cadre" in expected and plan.setting != expected["cadre"]:
        errors.append(f"cadre {plan.setting} au lieu de {expected['cadre']}")
    # Repère, pratique, moment de la journée et envie de « regarder »
    if "pres_de" in expected and (plan.near or ("",))[0] != expected["pres_de"]:
        errors.append(f"repère {plan.near} au lieu de {expected['pres_de']}")
    if "pratique" in expected and plan.practice != expected["pratique"]:
        errors.append(f"pratique {plan.practice} au lieu de {expected['pratique']}")
    if "moment" in expected and plan.moment != expected["moment"]:
        errors.append(f"moment {plan.moment} au lieu de {expected['moment']}")
    if "regarder" in expected and plan.watch != expected["regarder"]:
        errors.append(f"regarder {plan.watch} au lieu de {expected['regarder']}")
    return errors


# ----------------------------------------------------------------------------
# Précision des propositions
# ----------------------------------------------------------------------------

def card_errors(card: Mapping[str, Any], plan: QueryPlan, expected: Mapping[str, Any],
                words: Sequence[str], excluded_types: Sequence[str] = ()) -> list[str]:
    """Contraintes attendues qu'un événement proposé ne respecte pas.

    Parameters
    ----------
    card : Mapping[str, Any]
        La carte proposée (dates ISO, commune, zone, publics, prix, titre,
        résumé, lieu, catégorie, date affichée).
    plan : QueryPlan
        Le plan de recherche, qui donne la période et les mots de type.
    expected : Mapping[str, Any]
        Ce qui était attendu (section `compris` du YAML, plus `pas_pratique`).
    words : Sequence[str]
        Les mots attendus (déjà simplifiés) ; un seul trouvé suffit.
    excluded_types : Sequence[str]
        Les catégories refusées, sauf titre de participation (« Stage de
        chant »). Par défaut aucune.

    Returns
    -------
    list[str]
        Les contraintes non respectées (« date », « commune », « public »,
        « type », « heure »...) ; vide si la carte est correcte.
    """

    errors = []
    start = datetime.fromisoformat(card["start_at"])
    end = datetime.fromisoformat(card["end_at"]) if card.get("end_at") else start
    # Dans la période : il s'y déroule au moins en partie (une séance du jour
    # déjà commencée, un festival qui la chevauche).
    same_day = start.astimezone(PARIS).date() == plan.start.astimezone(PARIS).date()
    if not (start < plan.end and (end >= plan.start or same_day)):
        errors.append("date")
    # Lieu, public, gratuité, budget et cadre
    if expected.get("communes") and plain(card.get("city", "")) not in expected["communes"]:
        errors.append("commune")
    zone = expected.get("zone")
    if zone and card.get("zone") != {"montpellier": "montpellier", "thau": "bassin_de_thau"}[zone]:
        errors.append("zone")
    if expected.get("public"):
        labels = {"soiree": "soirée"}
        if labels.get(expected["public"], expected["public"]) not in card.get("audiences", []):
            errors.append("public")
    if expected.get("gratuit") and card.get("price") != "Gratuit":
        errors.append("gratuit")
    if "budget" in expected and card.get("price") != "Gratuit" and not (
            card.get("price_min") is not None and card["price_min"] <= float(expected["budget"])):
        errors.append("budget")
    if expected.get("cadre") == "interieur" and str(card.get("category", "")).lower() in OUTDOOR:
        errors.append("cadre")
    # Titre, résumé et lieu : « au musée Fabre » est souvent le lieu, pas le titre.
    text = plain(f"{card.get('title', '')} {card.get('summary', '')} {card.get('place', '')}")
    words_found = any(re.search(rf"\b{re.escape(word)}", text) for word in words)
    if words and not words_found:
        errors.append("mots")
    # Même règle que la recherche (15/10/2026) : du type demandé par sa
    # catégorie, ou parce que le type est écrit dans le titre (« Atelier
    # jeux ») ; le mot précis trouvé suffit (« Stage de cirque » pour « cirque »).
    if expected.get("types") and not words_found:
        # Titre, type et résumé : « Festival de cinéma » (CINÉMED), « Spectacle de danse ».
        title = plain(f"{card.get('title', '')} {card.get('category', '')} {card.get('summary', '')}")
        # Le type attendu, ou le mot de type de la question (« film », « balade »).
        kinds = [*expected["types"], *(word for word in plan.keywords if word in QUESTION_CATEGORIES)]
        in_title = any(re.search(rf"\b{re.escape(kind.rstrip('sx'))}", title) for kind in kinds)
        if str(card.get("category", "")).lower() not in expected["types"] and not in_title:
            errors.append("type")
    # « je veux chanter » : un concert n'est pas une réponse, sauf « Stage de chant ».
    if str(card.get("category", "")).lower() in excluded_types \
            and not PARTICIPATION.search(plain(str(card.get("title", "")))):
        errors.append("type exclu")
    # Moment de la journée (18/10/2026) : l'heure de début affichée sur la carte
    # (« Sam. 24 oct. · 14:30 ») ; une carte sans heure n'est pas contrôlée.
    shown = re.search(r"(\d{1,2}):\d{2}", str(card.get("date", "")))
    if shown and (expected.get("soir") or expected.get("moment")):
        hour = int(shown.group(1))
        first, last = (17, 24) if expected.get("soir") else MOMENT_HOURS[expected["moment"]]
        if not first <= hour < last:
            errors.append("heure")
    # « un spectacle de danse » : un bal ou un cours n'est pas une réponse.
    if expected.get("pas_pratique") and PRACTISED.search(
            plain(f"{card.get('title', '')} {card.get('category_label', '')}")):
        errors.append("pratique au lieu de spectacle")
    return errors


# ----------------------------------------------------------------------------
# Lancement
# ----------------------------------------------------------------------------

def evaluate(connection: Any, questions: Sequence[Mapping[str, Any]], services: SearchServices) -> dict[str, Any]:
    """Lance chaque question et calcule les mesures.

    Parameters
    ----------
    connection : Any
        La connexion PostgreSQL utilisée par la recherche.
    questions : Sequence[Mapping[str, Any]]
        Les questions du YAML (`question`, `filtres`, `compris`, `mots`,
        `pas_types`, `pas_pratique`).
    services : SearchServices
        Les services de recherche (embeddings, rédaction, horloge).

    Returns
    -------
    dict[str, Any]
        Les mesures globales (compréhension, précision, sans résultat, idées
        voisines, latences médiane et p95) et le détail question par question.
    """

    rows, latencies = [], []
    understood = correct_cards = total_cards = empty = neighbours = 0
    for item in questions:
        # Lire la question et ce qui en est attendu
        question = str(item["question"])
        filters = SearchFilters(**(item.get("filtres") or {}))
        expected = item.get("compris") or {}
        if item.get("pas_pratique"):
            expected = {**expected, "pas_pratique": True}
        words = [plain(word) for word in item.get("mots") or []]
        excluded = [plain(kind) for kind in item.get("pas_types") or []]
        # Vérifier la compréhension, puis lancer la vraie recherche en la chronométrant
        plan, _ = plan_search(question, filters, services.now())
        misunderstood = understanding_errors(plan, expected)
        started = time.monotonic()
        events = list(run_search(connection, question, filters, services))
        latencies.append(time.monotonic() - started)
        result = events[-1]["result"]
        relaxed = any("élargie" in (event.get("message") or "") for event in events)
        # Contrôler chaque carte proposée
        cards = result["events"]
        problems = [card_errors(card, plan, expected, words, excluded) for card in cards]
        good = sum(1 for errors in problems if not errors)
        exact = result.get("exact", True)
        understood += not misunderstood
        empty += not cards
        # Idées voisines (rien d'exact dans la base, et la réponse le dit) :
        # comptées à part, pas dans la précision.
        if exact:
            correct_cards += good
            total_cards += len(cards)
        else:
            neighbours += 1
        # Garder le détail de la question pour le rapport
        rows.append({
            "question": question, "compris": plan.describe(), "ecarts_comprehension": misunderstood,
            "propositions": len(cards), "correctes": good, "elargie": relaxed, "exact": exact,
            "erreurs": sorted({error for errors in problems for error in errors}),
            "titres": [card["title"] for card in cards],
            # Détail de chaque carte, pour comprendre un écart sans relancer.
            "cartes": [{"titre": card["title"], "type": card.get("category", ""), "publics": card.get("audiences", []),
                        "resume": card.get("summary", ""), "partenaire": card.get("partner", False),
                        "ecarts": errors} for card, errors in zip(cards, problems, strict=True)],
            "reponse": result["answer"] if services.write else "",
        })
    # Mesures globales sur l'ensemble des questions
    count = len(questions)
    return {
        "date": datetime.now(PARIS).isoformat(timespec="seconds"),
        "questions": count,
        "comprehension": round(understood / count, 3) if count else 0,
        "precision": round(correct_cards / total_cards, 3) if total_cards else 0,
        "sans_resultat": empty,
        "idees_voisines": neighbours,
        "latence_mediane_s": round(statistics.median(latencies), 2) if latencies else 0,
        "latence_p95_s": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 2) if latencies else 0,
        "detail": rows,
    }


def print_report(report: Mapping[str, Any]) -> None:
    """Rapport lisible dans le terminal.

    Chaque question est marquée « OK » (tout juste), « ~~ » (idées voisines)
    ou « -- » (au moins un écart), puis viennent les mesures globales.

    Parameters
    ----------
    report : Mapping[str, Any]
        Le résultat de `evaluate`.
    """

    # Détail question par question
    for row in report["detail"]:
        flag = ("OK " if not row["ecarts_comprehension"] and row["correctes"] == row["propositions"]
                else "~~ " if not row["exact"] else "-- ")
        print(f"{flag}{row['question']}")
        print(f"     compris : {row['compris']}")
        for error in row["ecarts_comprehension"]:
            print(f"     ! compréhension : {error}")
        print(f"     propositions correctes : {row['correctes']}/{row['propositions']}"
              + (f"  (écarts : {', '.join(row['erreurs'])})" if row["erreurs"] else "")
              + ("  [recherche élargie]" if row["elargie"] else "")
              + ("" if row["exact"] else "  [rien d'exact : idées voisines, hors précision]"))
        for title in row["titres"][:3]:
            print(f"       - {title}")
    # Mesures globales
    print()
    print(f"Questions               : {report['questions']}")
    print(f"Compréhension correcte  : {report['comprehension']:.0%}")
    print(f"Propositions correctes  : {report['precision']:.0%}  (objectif {TARGET:.0%}, réponses exactes)")
    print(f"Idées voisines          : {report['idees_voisines']}  (rien d'exact dans la base ; la réponse le dit)")
    print(f"Questions sans résultat : {report['sans_resultat']}")
    print(f"Temps de réponse        : médiane {report['latence_mediane_s']} s, 95 % sous {report['latence_p95_s']} s")


# ----------------------------------------------------------------------------
# Historique des lancements
# ----------------------------------------------------------------------------

def question_set_id(path: Path) -> str:
    """Calcule l'empreinte courte du jeu de questions.

    Empreinte courte du jeu de questions : deux scores ne se comparent que
    sur le même jeu.

    Parameters
    ----------
    path : Path
        Le fichier YAML des questions.

    Returns
    -------
    str
        Les 8 premiers caractères du SHA-256 du fichier.
    """

    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


def save_run(report: dict[str, Any], *, mode: str, question_file: Path, note: str,
             folder: Path = BENCH_DIR) -> Path:
    """Garde le lancement : rapport complet en JSON et une ligne dans historique.csv.

    Parameters
    ----------
    report : dict[str, Any]
        Le résultat de `evaluate` ; il n'est pas modifié.
    mode : str
        « recherche » ou « avec_redaction ».
    question_file : Path
        Le jeu de questions, dont l'empreinte est enregistrée.
    note : str
        Ce qui a changé depuis le dernier lancement.
    folder : Path
        Le dossier des lancements. Par défaut `benchmarks/recherche/`.

    Returns
    -------
    Path
        Le fichier JSON écrit (suffixé « _2 », « _3 »... si la minute est déjà prise).
    """

    # Compléter le rapport et choisir un nom de fichier libre
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(report["date"]).strftime("%Y-%m-%d_%Hh%M")
    report = {**report, "mode": mode, "jeu": question_set_id(question_file), "prompt_version": PROMPT_VERSION,
              "note": note}
    path = folder / f"{stamp}.json"
    number = 2
    while path.exists():        # deux lancements dans la même minute : ne pas écraser le premier
        path = folder / f"{stamp}_{number}.json"
        number += 1
    # Écrire le rapport complet, puis ajouter une ligne à l'historique
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    history = folder / "historique.csv"
    is_new = not history.exists()
    # utf-8-sig à la création : Excel (Windows) lit alors bien les accents.
    with history.open("a", encoding="utf-8-sig" if is_new else "utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS, delimiter=";")
        if is_new:
            writer.writeheader()
        writer.writerow({key: report.get(key, "") for key in HISTORY_COLUMNS})
    return path


def log_to_mlflow(report: Mapping[str, Any], json_path: Path, *, mode: str, question_file: Path,
                  note: str) -> str:
    """Même lancement dans MLflow (si actif et joignable). Renvoie un message.

    Parameters
    ----------
    report : Mapping[str, Any]
        Le résultat de `evaluate`.
    json_path : Path
        Le rapport JSON, joint au lancement.
    mode : str
        « recherche » ou « avec_redaction ».
    question_file : Path
        Le jeu de questions, dont l'empreinte est enregistrée.
    note : str
        Ce qui a changé depuis le dernier lancement.

    Returns
    -------
    str
        Le message de `log_run` (enregistré, ou pourquoi il ne l'a pas été).
    """

    from scripts.suivi_mlflow import log_run

    return log_run(
        MLFLOW_EXPERIMENT, f"évaluation {report['date'][:16]}",
        {"mode": mode, "jeu": question_set_id(question_file), "questions": report["questions"],
         "prompt_version": PROMPT_VERSION, "note": note},
        {key: report[key] for key in ("comprehension", "precision", "idees_voisines", "sans_resultat",
                                      "latence_mediane_s", "latence_p95_s")},
        [json_path])


def main() -> None:
    """Lance l'évaluation, affiche le rapport et le garde (sauf en mode CI).

    Avec `--jeu-test`, la recherche tourne sur la base de test figée, sans
    vecteur ni Mistral, et rien n'est gardé dans `benchmarks/`. Les options
    `--sans-...` retirent une règle de compréhension pour mesurer l'« avant ».

    Raises
    ------
    SystemExit
        Si `--seuil` est donné et que la précision ou la compréhension passe dessous.
    """
    # Lire les options de la ligne de commande
    parser = argparse.ArgumentParser(description="Évaluation de la recherche SortirIA.")
    parser.add_argument("--avec-redaction", action="store_true", help="Fait aussi rédiger la réponse par Mistral.")
    parser.add_argument("--json", type=Path, help="Copie aussi le rapport complet dans ce fichier.")
    parser.add_argument("--note", default="", help="Ce qui a changé depuis le dernier lancement (historique).")
    parser.add_argument("--fichier", type=Path, default=QUESTIONS_FILE, help="Jeu de questions (YAML).")
    parser.add_argument("--jeu-test", action="store_true",
                        help="Base de test figée (CI) : horloge à l'instant de l'export, sans vecteur ni historique.")
    parser.add_argument("--sans-pratique", action="store_true",
                        help="Retire la règle des verbes de pratique (mesure « avant », 17/10/2026).")
    parser.add_argument("--sans-regarder", action="store_true",
                        help="Retire la règle « regarder » (un bal n'est pas un spectacle) : mesure « avant ».")
    parser.add_argument("--sans-moment", action="store_true",
                        help="Retire le moment de la journée lu dans la phrase (mesure « avant », 18/10/2026).")
    parser.add_argument("--seuil", type=float, default=None,
                        help="Échoue (code 1) si la précision ou la compréhension passe sous ce seuil (ex. 0.95).")
    args = parser.parse_args()

    load_environment()
    from agent.services import connect_postgres

    # Ablations : retirer une règle de compréhension pour mesurer l'« avant »
    if args.sans_pratique:
        # Ablation : la recherche telle qu'avant le 17/10/2026, pour comparer.
        from app import query_understanding

        query_understanding.PRACTICE_VERBS.clear()
        query_understanding.PRACTICE_PHRASES.clear()
        args.note = f"{args.note} (sans verbes de pratique)".strip()
    if args.sans_moment:
        from app import query_understanding

        query_understanding.MOMENT_PHRASES = {}
        query_understanding.EVENING_PHRASES = ()
        args.note = f"{args.note} (sans moment de la journée)".strip()
    if args.sans_regarder:
        from app import query_understanding

        query_understanding.WATCH_WORDS = ()
        query_understanding.SPECTACLE_YIELDS = False
        args.note = f"{args.note} (sans « regarder »)".strip()

    # Charger les questions et choisir les services (réels, ou base de test figée)
    questions = yaml.safe_load(args.fichier.read_text(encoding="utf-8"))["questions"]
    services = SearchServices(embed=embed_question, write=write_with_mistral if args.avec_redaction else None,
                              log=False, cache=False)
    if args.jeu_test:
        from scripts.jeu_test_recherche import snapshot_time

        frozen = snapshot_time().astimezone(PARIS)
        services = SearchServices(embed=None, write=None, now=lambda: frozen, log=False, cache=False)
        print(f"Base de test figée au {frozen:%d/%m/%Y %H:%M}, recherche sans vecteur ni Mistral.\n")
    # Évaluer et afficher le rapport (base figée : son horloge aussi, pour les vues)
    with connect_postgres() as connection:
        if args.jeu_test:
            from scripts.jeu_test_recherche import freeze_clock

            freeze_clock(connection, frozen)
        report = evaluate(connection, questions, services)
    print_report(report)
    failed = below_threshold(report, args.seuil)
    if args.jeu_test:
        if args.json:
            args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if failed:
            raise SystemExit(f"\nÉchec : {failed}")
        return
    # Hors CI : garder le lancement (JSON, historique, MLflow)
    mode = "avec_redaction" if args.avec_redaction else "recherche"
    saved = save_run(report, mode=mode, question_file=args.fichier, note=args.note)
    print(f"\nLancement gardé : {saved.relative_to(ROOT)} et {BENCH_DIR.relative_to(ROOT)}/historique.csv")
    print(log_to_mlflow(report, saved, mode=mode, question_file=args.fichier, note=args.note))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nRapport enregistré dans {args.json}")
    if failed:
        raise SystemExit(f"\nÉchec : {failed}")


def below_threshold(report: Mapping[str, Any], threshold: float | None) -> str:
    """Ce qui passe sous le seuil (vide si tout va bien, ou sans seuil).

    Parameters
    ----------
    report : Mapping[str, Any]
        Le résultat de `evaluate`.
    threshold : float | None
        Le seuil minimal de précision et de compréhension, ou None.

    Returns
    -------
    str
        Les mesures sous le seuil, séparées par « ; » ; vide sinon.
    """

    if threshold is None:
        return ""
    misses = [f"{label} {report[key]:.1%} < {threshold:.0%}"
              for key, label in (("precision", "précision"), ("comprehension", "compréhension"))
              if report[key] < threshold]
    return " ; ".join(misses)


if __name__ == "__main__":
    main()
