"""Fichiers des partenaires (tableur Excel ou CSV, PDF ou Word), importés chaque nuit.

**Provenance** : nouvel adaptateur écrit pour SortirIA (14/10/2026), branché
sur le moteur datasets de CheckIt.AI (`src/extractors/datasets/`) : le moteur
trouve et contrôle les fichiers, filtre, dédoublonne et rend un statut ; cet
adaptateur ne fait que lire les lignes et les convertir (voir
`src/event/partner_mapper.py`).

Organisation sur le disque (dossier `path` de la source, `data/partenaires`) :
**un fichier par partenaire, à la racine, nommé d'après lui**.

    data/partenaires/
        saintlouis.xlsx           <- partenaire « Saintlouis »
        bar_truc_muche.csv        <- partenaire « Bar Truc Muche »
        _modeles/                 <- sous-dossier : jamais importé

Tout fichier .xlsx, .xls, .csv ou .json de la racine est importé (sauf ceux qui
commencent par « _ », « . » ou « ~$ », fichier ouvert dans Excel). Le nom
du partenaire vient du nom du fichier ; une entrée facultative dans
`config/sources.yaml` (`partners`, clé `file`) précise son vrai nom, son
secteur, son site et la fin d'une mise en avant, ou le désactive.

**PDF et Word** (16/10/2026) : une plaquette ou un programme mis en page
librement est lu par Mistral (`partner_documents.py`), qui range chaque
séance dans les colonnes du modèle ; les lignes passent ensuite par les mêmes
contrôles qu'un tableur. Un tel document n'est importé **qu'après validation
humaine** : tant qu'il n'est pas validé, il est lu, compté (motif
`document_a_valider`) et l'agent propose de l'importer, avec un aperçu. Le
lieu et la commune, souvent implicites sur la plaquette d'un lieu, peuvent
être déclarés pour le partenaire dans `config/sources.yaml` (`venue`,
`city`).

Vérifier un fichier reçu avant de le déposer (aucune écriture en base) :

    uv run python -m src.extractors.datasets.source.partner_extractor --verifier chemin/du/fichier.xlsx
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from src.event.event_normalizer import PARIS
from src.event.event_pipeline import occurrence_article_url
from src.event.partner_mapper import (
    REASON_MISSING_COLUMNS,
    REASON_UNREADABLE_FILE,
    TEMPLATE_COLUMNS,
    is_empty_row,
    map_partner_row,
    missing_columns,
    normalize_header,
    partner_formula,
)
from src.extractors.datasets.dataset_adapter import DatasetAdapter
from src.extractors.datasets.dataset_engine import DatasetExtractor
from src.extractors.datasets.dataset_file_utils import resolve_dataset_directory
from src.extractors.datasets.source.partner_documents import (
    DOCUMENT_EXTENSIONS,
    REASON_TO_APPROVE,
    file_sha256,
    is_approved,
    is_document,
    read_document_rows,
)
from src.logger import get_logger
from src.utils.extractor_utils import build_standard_article
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)

SOURCE_ID = "partenaire"
# .xls (ancien format Excel) : lu par la bibliothèque xlrd.
SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".csv", ".json"}) | DOCUMENT_EXTENSIONS
IGNORED_PREFIXES = ("_", ".", "~$")
# Secteurs acceptés par la table `partners` (contrainte partners_sector_check).
SECTORS = ("animation", "soiree", "enfants", "culture", "sport", "autre")


# ---------------------------------------------------------------------------
# Partenaires et fichiers
# ---------------------------------------------------------------------------

def partner_key(value: Any) -> str:
    """Clé de rapprochement fichier / configuration : « Saint-Louis » = « saint_louis ».

    Parameters
    ----------
    value : Any
        Nom de fichier ou nom déclaré.

    Returns
    -------
    str
        Le texte en minuscules réduit à ses lettres a-z et chiffres.
    """

    return re.sub(r"[^a-z0-9]", "", normalize_value(value).casefold())


def name_from_file(stem: str) -> str:
    """Nom lisible tiré du nom de fichier : « bar_truc-muche » -> « Bar Truc Muche ».

    Parameters
    ----------
    stem : str
        Nom du fichier sans extension.

    Returns
    -------
    str
        Les mots séparés par des espaces, chacun avec une majuscule ; `stem` tel
        quel s'il n'y a aucun mot.
    """

    return " ".join(word.capitalize() for word in re.split(r"[_\-\s]+", stem) if word) or stem


def declared_partners(source: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Réglages facultatifs de `partners` dans la configuration, par clé de fichier.

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (liste `partners`, chaque entrée avec `file`).

    Returns
    -------
    dict[str, dict[str, Any]]
        Les réglages de chaque partenaire, indexés par `partner_key(file)`.
    """

    declared = {}
    for partner in source.get("partners") or []:
        if isinstance(partner, Mapping) and partner_key(partner.get("file")):
            declared[partner_key(partner.get("file"))] = dict(partner)
    return declared


def partner_for_file(path: Path, source: Mapping[str, Any]) -> dict[str, Any]:
    """Partenaire d'un fichier : son nom de fichier, complété par la configuration.

    Parameters
    ----------
    path : Path
        Chemin du fichier du partenaire.
    source : Mapping[str, Any]
        Configuration de la source.

    Returns
    -------
    dict[str, Any]
        Les réglages déclarés, plus `file`, `name`, `sector` (« autre » si
        inconnu), `formula` et `enabled` (vrai sauf `enabled: false` explicite).
    """

    settings = declared_partners(source).get(partner_key(path.stem), {})
    sector = normalize_value(settings.get("sector")).lower()
    return {
        **settings,
        "file": path.stem,
        "name": normalize_value(settings.get("name")) or name_from_file(path.stem),
        "sector": sector if sector in SECTORS else "autre",
        "formula": partner_formula(settings),
        "enabled": settings.get("enabled", True) is not False,
    }


def find_partner_files(directory: Path, source: Mapping[str, Any]) -> list[Path]:
    """Fichiers de partenaires à la racine du dossier (sous-dossiers ignorés).

    Sont écartés : les extensions non prises en charge, les noms commençant par
    « _ », « . » ou « ~$ », et les partenaires désactivés dans la configuration.

    Parameters
    ----------
    directory : Path
        Dossier des fichiers partenaires.
    source : Mapping[str, Any]
        Configuration de la source.

    Returns
    -------
    list[Path]
        Les fichiers à importer, triés par nom.
    """

    files = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if path.name.startswith(IGNORED_PREFIXES):
            continue
        if not partner_for_file(path, source)["enabled"]:
            logger.info("Partenaire désactivé dans la configuration : %s.", path.name)
            continue
        files.append(path)
    return files


def has_partner_files(source: Mapping[str, Any]) -> bool:
    """Vrai si le dossier existe et contient au moins un fichier à importer (pour l'agent).

    Parameters
    ----------
    source : Mapping[str, Any]
        Configuration de la source (clé `path`).

    Returns
    -------
    bool
        False aussi si le dossier est absent ou mal configuré.
    """

    try:
        directory = resolve_dataset_directory(source, str(source.get("name", SOURCE_ID)))
    except (OSError, ValueError):
        return False
    return bool(find_partner_files(directory, source))


def read_json_rows(path: Path) -> list[dict[str, Any]]:
    """Lignes d'un fichier JSON : une liste d'objets, ou un objet qui en contient une.

    Exemples acceptés : `[{"titre": ...}, ...]` ou `{"evenements": [...]}`
    (les clés sont celles du modèle, comme les colonnes d'un tableur).

    Parameters
    ----------
    path : Path
        Chemin du fichier JSON (UTF-8, avec ou sans BOM).

    Returns
    -------
    list[dict[str, Any]]
        Les objets de la liste ; les éléments qui ne sont pas des objets sont
        ignorés.

    Raises
    ------
    ValueError
        Si le fichier ne contient pas de liste (ou si le JSON est invalide).
    """

    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(data, Mapping):
        data = next((value for value in data.values() if isinstance(value, list)), [])
    if not isinstance(data, list):
        raise ValueError(f"{path.name} : une liste d'événements est attendue.")  # noqa: TRY004 (le moteur arrête proprement sur ValueError)
    return [dict(item) for item in data if isinstance(item, Mapping)]


def read_table(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    """En-têtes normalisés et lignes d'un fichier (1er onglet d'un Excel).

    CSV : séparateur deviné (« ; » d'un Excel français ou « , »), encodage
    UTF-8 avec ou sans BOM, sinon Windows-1252. Les cellules sont gardées telles quelles (dates
    Excel comprises) : `partner_mapper` se charge de les lire et de les
    nettoyer.

    PDF et Word sont lus par Mistral (`read_document_rows`) ; leurs en-têtes sont
    alors ceux du modèle partenaire.

    Parameters
    ----------
    path : Path
        Chemin du fichier (.xlsx, .xls, .csv, .json, .pdf ou .docx).

    Returns
    -------
    tuple[list[str], list[dict[str, Any]]]
        Les en-têtes normalisés, et les lignes indexées par ces en-têtes.
    """

    import pandas as pd

    if is_document(path):
        # PDF ou Word : Mistral range chaque séance dans les colonnes du modèle.
        rows = [{normalize_header(key): value for key, value in row.items()} for row in read_document_rows(path)]
        return [name for name, _, _ in TEMPLATE_COLUMNS], rows
    if path.suffix.lower() == ".json":
        rows = [{normalize_header(key): value for key, value in row.items()} for row in read_json_rows(path)]
        headers = list(dict.fromkeys(key for row in rows for key in row))
        return headers, rows
    if path.suffix.lower() == ".csv":
        try:
            frame = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="utf-8-sig", keep_default_na=False)
        except UnicodeDecodeError:
            # Excel français enregistre souvent en Windows-1252 (« Soirée » illisible en UTF-8).
            frame = pd.read_csv(path, sep=None, engine="python", dtype=str, encoding="cp1252", keep_default_na=False)
    else:
        frame = pd.read_excel(path, sheet_name=0, dtype=object)
    headers = [normalize_header(column) for column in frame.columns]
    frame.columns = headers
    return headers, frame.to_dict(orient="records")


# ---------------------------------------------------------------------------
# Lecture des lignes
# ---------------------------------------------------------------------------

def read_partner_file(path: Path, partner: Mapping[str, Any], today: Any = None,
                      require_approval: bool = True) -> list[dict[str, Any]]:
    """Lit un fichier : un élément par ligne utile, événement ou rejet motivé.

    Le fichier est lu en entier avant de rendre ses lignes, pour joindre à
    chaque événement le bilan du fichier (lignes lues, gardées, refusées),
    enregistré ensuite dans `partner_imports`.

    Parameters
    ----------
    path : Path
        Chemin du fichier du partenaire.
    partner : Mapping[str, Any]
        Le partenaire (voir `partner_for_file`) : nom, secteur, lieu, commune...
    today : Any
        Date de référence pour écarter les séances passées ; par défaut
        aujourd'hui à Paris.
    require_approval : bool
        Exiger la validation humaine d'un PDF ou Word avant de rendre ses
        événements. Par défaut True ; `check_file` passe False.

    Returns
    -------
    list[dict[str, Any]]
        Un élément par ligne utile : `{"event", "line"}` ou `{"rejected", "line"}`
        (avec parfois `detail`). Un seul rejet si des colonnes obligatoires
        manquent, ou si le document attend sa validation (avec un aperçu).
    """

    # Lire le fichier et vérifier les colonnes obligatoires
    now = datetime.now(PARIS)
    today = today or now.date()
    updated_at = datetime.fromtimestamp(path.stat().st_mtime, PARIS).date().isoformat()
    headers, rows = read_table(path)
    absent = missing_columns(headers)
    if absent:
        logger.error("%s : colonnes obligatoires absentes : %s.", path.name, ", ".join(absent))
        return [{"rejected": REASON_MISSING_COLUMNS, "line": 1, "detail": ", ".join(absent)}]

    # Convertir chaque ligne en événement, en rejet motivé, ou l'écarter (passée)
    document = is_document(path)
    items, reasons, past = [], Counter(), 0
    for number, row in enumerate(rows, start=1 if document else 2):   # tableur : ligne 1 = en-têtes
        if document:
            # Lieu et commune implicites sur la plaquette d'un lieu : ceux du partenaire.
            row = {**row, "lieu": row.get("lieu") or partner.get("venue", ""),
                   "commune": row.get("commune") or partner.get("city", "")}
            if row.get("rejet_llm"):
                reasons[str(row["rejet_llm"])] += 1
                items.append({"rejected": str(row["rejet_llm"]), "line": number,
                              "detail": f"page {row.get('page_llm', '?')} : {row.get('titre', '')}".strip()})
                continue
        if is_empty_row(row):
            continue
        event, reason = map_partner_row(row, partner, today=today, updated_at=updated_at, extracted_at=now.isoformat())
        if event is not None:
            items.append({"event": event, "line": number})
        elif reason:
            reasons[reason] += 1
            items.append({"rejected": reason, "line": number})
        else:
            past += 1

    # Document pas encore validé : ne rien publier, rendre un aperçu à proposer
    kept = sum(1 for item in items if "event" in item)
    if document and require_approval and not is_approved(path):
        # Lu par Mistral mais pas encore validé : rien n'est publié ; l'agent
        # propose l'import avec un aperçu (agent/nodes.py, watch).
        logger.info("%s / %s : %s séance(s) lue(s) par Mistral, en attente de validation.",
                    partner.get("name"), path.name, kept)
        return [{"rejected": REASON_TO_APPROVE, "line": 0,
                 "detail": f"{kept} séance(s) lue(s) par Mistral, à valider (uv run python -m agent.proposals)",
                 "preview": [item["event"] for item in items if "event" in item]}]
    # Joindre à chaque événement le bilan du fichier (enregistré dans partner_imports)
    summary = {
        "name": partner.get("name"), "file": partner.get("file"), "sector": partner.get("sector"),
        "website": partner.get("website", ""), "sponsored_until": partner.get("sponsored_until"),
        "formula": partner.get("formula") or partner_formula(partner),
        "file_name": path.name, "file_sha256": file_sha256(path),
        "rows_total": kept + sum(reasons.values()) + past, "rows_loaded": kept,
        "rows_rejected": sum(reasons.values()), "rejection_reasons": dict(reasons),
    }
    for item in items:
        if "event" in item:
            item["event"].partner_info = summary
    logger.info(
        "%s / %s : %s ligne(s) gardée(s), %s refusée(s), %s déjà passée(s).",
        partner.get("name"), path.name, kept, sum(reasons.values()), past,
    )
    return items


def iter_partner_items(files: list[Path], source: Mapping[str, Any]) -> Iterator[tuple[str, int, dict[str, Any]]]:
    """Éléments de tous les fichiers, au format du moteur : (identifiant, index, élément).

    Un fichier illisible donne un seul élément rejeté (`REASON_UNREADABLE_FILE`)
    sans empêcher la lecture des autres.

    Parameters
    ----------
    files : list[Path]
        Fichiers retenus par `find_partner_files`.
    source : Mapping[str, Any]
        Configuration de la source.

    Yields
    ------
    tuple[str, int, dict[str, Any]]
        L'identifiant `fichier:L<ligne>`, le numéro de ligne et l'élément.
    """

    for path in files:
        try:
            items = read_partner_file(path, partner_for_file(path, source))
        except Exception as error:  # noqa: BLE001 (un fichier illisible ne doit pas priver la nuit des autres)
            logger.error("%s : fichier illisible (%s).", path.name, error)
            yield f"{path.name}:L0", 0, {"rejected": REASON_UNREADABLE_FILE, "line": 0, "detail": str(error)}
            continue
        for item in items:
            yield f"{path.name}:L{item['line']}", item["line"], item


def validate_partner_item(item: Any, filters: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[bool, str]:
    """Une ligne refusée remonte au moteur avec son motif (compté dans le bilan).

    Parameters
    ----------
    item : Any
        Élément produit par `iter_partner_items`.
    filters : Mapping[str, Any]
        Filtres de la source (non utilisés).
    source : Mapping[str, Any]
        Configuration de la source (non utilisée).

    Returns
    -------
    tuple[bool, str]
        `(False, motif)` pour une ligne rejetée, sinon `(True, "")`.
    """

    del filters, source
    if isinstance(item, Mapping) and item.get("rejected"):
        return False, str(item["rejected"])
    return True, ""


def build_partner_article(item: Mapping[str, Any], index: int, identifier: str, source: Mapping[str, Any]) -> dict[str, Any]:
    """Transforme une ligne validée en « article » du moteur, événement inclus.

    Parameters
    ----------
    item : Mapping[str, Any]
        Élément validé, avec son `event` et sa `line`.
    index : int
        Position de l'élément (non utilisée).
    identifier : str
        Identifiant `fichier:L<ligne>`.
    source : Mapping[str, Any]
        Configuration de la source (nom, rôle).

    Returns
    -------
    dict[str, Any]
        L'article standard, avec l'URL propre à la séance et l'événement
        sérialisé dans `metadata`.
    """

    del index
    event = item["event"]
    return build_standard_article(
        identifier=identifier,
        source=event.source_credit or source.get("name", "Partenaire"),
        title=event.title,
        text=event.description,
        image_url="",
        published_at=event.start_at,
        url=occurrence_article_url(event),   # unique par séance (voir event_pipeline)
        language="fr",
        category=event.category or "evenement",
        role=source.get("role", "acquisition"),
        metadata={"event": event.to_dict(), "line": item.get("line")},
    )


PARTNER_ADAPTER = DatasetAdapter(
    source_id=SOURCE_ID,
    default_name="Fichiers des partenaires",
    supported_extensions=SUPPORTED_EXTENSIONS,
    find_files=find_partner_files,
    iter_items=iter_partner_items,
    build_article=build_partner_article,
    validate_item=validate_partner_item,
)

PARTNER_EXTRACTOR = DatasetExtractor(
    source_id=SOURCE_ID,
    default_name="Fichiers des partenaires",
    adapter=PARTNER_ADAPTER,
)


def extract_all_articles():
    """Lit tous les fichiers des partenaires déclarés (point d'entrée du registre).

    Returns
    -------
    ExtractorResult
        Le résultat du moteur datasets pour la source « partenaire ».
    """

    return PARTNER_EXTRACTOR.run()


# ---------------------------------------------------------------------------
# Vérification d'un fichier reçu
# ---------------------------------------------------------------------------

def configured_partner(path: Path) -> dict[str, Any]:
    """Partenaire d'un fichier d'après la configuration (sinon : son nom de fichier).

    Parameters
    ----------
    path : Path
        Chemin du fichier.

    Returns
    -------
    dict[str, Any]
        Le partenaire ; une configuration illisible est journalisée et ignorée.
    """

    try:
        return partner_for_file(path, PARTNER_EXTRACTOR.get_source())
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Configuration des partenaires illisible : %s", error)
        return partner_for_file(path, {})


def check_file(path: Path, partner: Mapping[str, Any] | None = None) -> str:
    """Rapport lisible sur un fichier, à renvoyer au partenaire si besoin.

    Sans site déclaré pour ce partenaire dans la configuration, chaque ligne
    doit avoir son lien.

    Parameters
    ----------
    path : Path
        Chemin du fichier à vérifier.
    partner : Mapping[str, Any] | None
        Le partenaire ; par défaut celui déduit de la configuration.

    Returns
    -------
    str
        Le rapport sur plusieurs lignes : bilan, lignes à corriger avec leur
        motif, puis un échantillon de séances prêtes (5, ou 50 pour un document).
    """

    # Lire le fichier sans exiger de validation (aucune écriture en base)
    partner = partner or configured_partner(path)
    items = read_partner_file(path, partner, require_approval=False)
    kept = [item for item in items if "event" in item]
    rejected = [item for item in items if "rejected" in item]
    lines = [f"{path.name} : {len(kept)} séance(s) à venir prête(s), {len(rejected)} ligne(s) à corriger."]
    if is_document(path):
        state = "déjà validé" if is_approved(path) else "à valider avant import (proposition de l'agent)"
        lines[0] += f" Document lu par Mistral, {state}."
    # Détailler les lignes à corriger, puis un échantillon des séances prêtes
    for item in rejected:
        detail = f" ({item['detail']})" if item.get("detail") else ""
        lines.append(f"  ligne {item['line']} : {item['rejected']}{detail}")
    for item in kept[:5 if not is_document(path) else 50]:
        event = item["event"]
        lines.append(f"  ok ligne {item['line']} : {event.start_at[:16]} {event.title} - {event.venue_name}, {event.city}")
    return "\n".join(lines)


def main() -> None:
    """Point d'entrée en ligne de commande.

    Avec `--verifier`, affiche le rapport d'un fichier reçu ; sans option, lance
    l'import complet et affiche son bilan.
    """
    parser = argparse.ArgumentParser(description="Fichiers des partenaires SortirIA.")
    parser.add_argument("--verifier", type=Path, help="Vérifie un fichier reçu (aucune écriture en base).")
    args = parser.parse_args()
    if args.verifier:
        if not args.verifier.is_file():
            formats = ", ".join(sorted(SUPPORTED_EXTENSIONS))
            print(
                f"Fichier introuvable : {args.verifier}. "
                f"Donner le chemin d'un fichier reçu ({formats})."
            )
            return
        print(check_file(args.verifier))
        return
    result = extract_all_articles()
    print(f"{len(result.articles)} événement(s). Statut : {result.status}. Rejets : {result.rejection_reasons}")


if __name__ == "__main__":
    main()
