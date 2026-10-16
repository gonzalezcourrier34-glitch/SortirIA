"""Documents des partenaires (PDF, Word) lus par Mistral, validés par un humain.

**Provenance** : nouveau module écrit pour SortirIA (16/10/2026), sur le
modèle de la lecture des programmes PDF des collectivités
(`src/extractors/documents/document_extractor.py`, `src/event/document_mapper.py`).

Un partenaire n'envoie pas toujours le tableur modèle : une plaquette PDF, un
programme Word, chacun mis en page à sa façon. Un ETL ne sait lire que des
colonnes connues ; ici, Mistral lit le texte et en tire des événements, rangés
dans **les colonnes du modèle partenaire** (titre, date, heure, lieu,
commune, prix, public…). Ensuite, tout est comme pour un tableur : chaque
ligne passe par `partner_mapper.map_partner_row` (mêmes contrôles, mêmes
motifs de rejet), et l'offre est signalée « partenaire ».

Garde-fous :

1. **le titre doit figurer dans le texte** lu (au moins 60 % de ses mots
   significatifs) : un événement inventé est rejeté (`titre_absent_du_texte`) ;
2. **validation humaine** : un document lu par Mistral n'est publié qu'après
   accord. La première nuit, l'agent le lit, compte les événements et
   **propose** de l'importer, avec un aperçu (mail de bilan,
   `uv run python -m agent.proposals`). Accepter la proposition inscrit le
   fichier et son empreinte dans `_documents_valides.json` (dossier des
   partenaires) ; un document modifié (autre empreinte) redemande un accord ;
3. **cache** par empreinte : un document inchangé n'est pas relu par Mistral.

Vérifier un document reçu avant de le déposer (lecture par Mistral, aucune
écriture en base) :

    uv run python -m src.extractors.datasets.source.partner_extractor --verifier programme.docx
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from config.paths import DATA_DIR
from src.event.document_mapper import (
    REASON_INVALID_JSON,
    REASON_TITLE_NOT_IN_TEXT,
    parse_llm_response,
    title_found_in_text,
)
from src.event.event_normalizer import PARIS
from src.logger import get_logger

logger = get_logger(__name__)

DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx"})
APPROVALS_FILE_NAME = "_documents_valides.json"
CACHE_DIR = DATA_DIR / "cache" / "partenaires"
PROMPT_VERSION = "2026-10-16.1"
DEFAULT_MODEL = "mistral-small-latest"
PARAGRAPHS_PER_PAGE = 40          # un document Word n'a pas de pages : on le découpe en paquets
MAX_CHUNK_CHARS = 15000
REASON_TO_APPROVE = "document_a_valider"

LlmCall = Callable[[str, str], tuple[str, dict[str, int]]]

EXTRACTION_PROMPT = """Tu lis le document d'un partenaire de SortirIA (bar, salle, club,
ludothèque, festival…) près de Montpellier ou de l'étang de Thau : un programme, une
plaquette, une affiche. Nous sommes le {today}.

Rends chaque séance d'événement à venir qui figure dans le texte, une ligne par séance
(un même événement à trois dates = trois lignes), avec ces champs :
- "titre" : le nom de l'événement, recopié tel qu'il est écrit ;
- "date" : JJ/MM/AAAA (si l'année manque, la prochaine occurrence après le {today}) ;
- "heure_debut", "heure_fin" : HH:MM, ou "" si absente ;
- "date_fin" : JJ/MM/AAAA pour un événement sur plusieurs jours, sinon "" ;
- "lieu", "adresse", "code_postal", "commune" : tels qu'écrits ; "" si absents ;
- "prix" : tel qu'écrit (« 12 € », « Gratuit »), sinon "" ; "gratuit" : "oui" ou "" ;
- "public" : tel qu'écrit (« famille », « dès 6 ans », « adultes »), sinon "" ;
- "age_min", "age_max" : nombres si écrits, sinon "" ;
- "categorie" : le genre écrit (« Concert », « Atelier », « Soirée DJ »…), sinon "" ;
- "description" : une ou deux phrases du texte, sans rien ajouter ;
- "lien" : une adresse https du texte, sinon "".
N'invente RIEN : ni événement, ni date, ni lieu, ni prix. Ce qui n'est pas écrit reste "".
Ignore les horaires d'ouverture habituels, les mentions légales, les publicités.

Réponds uniquement avec un objet JSON : {{"evenements": [ {{...}}, ... ]}}
S'il n'y a aucun événement, réponds {{"evenements": []}}."""


# ---------------------------------------------------------------------------
# Texte d'un document
# ---------------------------------------------------------------------------

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_paragraphs(data: bytes) -> list[str]:
    """Paragraphes d'un document Word (.docx), tableaux compris, sans bibliothèque de plus.

    Un .docx est une archive zip ; le texte est dans `word/document.xml`. Une
    ligne de tableau devient un paragraphe (cellules séparées par « | »).
    """

    import io

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{WORD_NS}body")
    paragraphs: list[str] = []

    def text_of(element: ElementTree.Element) -> str:
        parts = []
        for node in element.iter():
            if node.tag == f"{WORD_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{WORD_NS}tab", f"{WORD_NS}br"):
                parts.append(" ")
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    for block in list(body) if body is not None else []:
        if block.tag == f"{WORD_NS}p":
            text = text_of(block)
            if text:
                paragraphs.append(text)
        elif block.tag == f"{WORD_NS}tbl":
            for row in block.iter(f"{WORD_NS}tr"):
                cells = [text_of(cell) for cell in row.iter(f"{WORD_NS}tc")]
                if any(cells):
                    paragraphs.append(" | ".join(cells))
    return paragraphs


def document_pages(path: Path) -> list[str]:
    """Texte du document, par « page » : pages d'un PDF, paquets de paragraphes d'un Word."""

    data = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        from src.extractors.documents.document_extractor import extract_pages_text

        return [page for page in extract_pages_text(data) if page.strip()]
    paragraphs = docx_paragraphs(data)
    return ["\n".join(paragraphs[start:start + PARAGRAPHS_PER_PAGE])
            for start in range(0, len(paragraphs), PARAGRAPHS_PER_PAGE)]


# ---------------------------------------------------------------------------
# Lecture par Mistral, avec cache
# ---------------------------------------------------------------------------

def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cache_path(sha256: str) -> Path:
    model = os.getenv("DOCUMENT_EXTRACTION_MODEL", DEFAULT_MODEL).replace("/", "_")
    return CACHE_DIR / f"{sha256[:16]}-{PROMPT_VERSION}-{model}.json"


def default_llm(prompt: str, text: str) -> tuple[str, dict[str, int]]:
    from src.extractors.documents.document_extractor import call_mistral, call_with_rate_limit

    return call_with_rate_limit(call_mistral, prompt, text)


def read_document_rows(path: Path, llm: LlmCall | None = None, today: date | None = None,
                       use_cache: bool = True) -> list[dict[str, Any]]:
    """Lignes « modèle partenaire » tirées du document par Mistral.

    Chaque ligne garde `page_llm` ; une ligne dont le titre est absent du texte
    de sa page est rendue avec `rejet_llm` = `titre_absent_du_texte`.
    Le résultat est mis en cache par empreinte du fichier.
    """

    sha256 = file_sha256(path)
    cached = cache_path(sha256)
    if use_cache and cached.exists():
        try:
            return with_document_link(json.loads(cached.read_text(encoding="utf-8")), path)
        except ValueError:
            logger.warning("Cache illisible pour %s : le document est relu.", path.name)

    from src.extractors.documents.document_extractor import record_token_usage

    llm = llm or default_llm
    today = today or datetime.now(PARIS).date()
    prompt = EXTRACTION_PROMPT.format(today=today.strftime("%d/%m/%Y"))
    rows: list[dict[str, Any]] = []
    for number, page in enumerate(document_pages(path), start=1):
        answer, usage = llm(prompt, page[:MAX_CHUNK_CHARS])
        record_token_usage(os.getenv("DOCUMENT_EXTRACTION_MODEL", DEFAULT_MODEL), usage)
        try:
            raw_events = parse_llm_response(answer)
        except (ValueError, TypeError):
            rows.append({"page_llm": number, "rejet_llm": REASON_INVALID_JSON})
            continue
        for raw in raw_events:
            row = {str(key): ("" if value is None else value) for key, value in raw.items()}
            row["page_llm"] = number
            if not title_found_in_text(str(row.get("titre") or ""), page):
                row["rejet_llm"] = REASON_TITLE_NOT_IN_TEXT
            rows.append(row)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = cached.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, cached)
    logger.info("%s : %s ligne(s) lue(s) par Mistral.", path.name, len(rows))
    return with_document_link(rows, path)


URL = re.compile(r"https?://[^\s<>\"')\]]+")


def with_document_link(rows: list[dict[str, Any]], path: Path) -> list[dict[str, Any]]:
    """Lien d'une séance absent : celui que le document donne pour tout le programme.

    Essai du 16/10/2026 sur la plaquette d'exemple : Mistral a bien lu les 4
    séances, mais le lien « Programme et réservations : https://… » est écrit
    une fois, en bas du document ; aucune séance n'en avait, toutes étaient
    refusées (« lien manquant »). Ce lien est dans le texte : rien d'inventé.
    """

    links = [match.group(0).rstrip(".,;:") for page in document_pages(path) for match in URL.finditer(page)]
    if not links:
        return rows
    return [{**row, "lien": row.get("lien") or links[0]} if not row.get("rejet_llm") else row for row in rows]


def cached_rows(path: Path) -> list[dict[str, Any]] | None:
    """Lignes déjà lues (cache), sans appeler Mistral ; None si le document n'a pas été lu."""

    cached = cache_path(file_sha256(path))
    if not cached.exists():
        return None
    try:
        return json.loads(cached.read_text(encoding="utf-8"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Validation humaine
# ---------------------------------------------------------------------------

def approvals_path(directory: Path) -> Path:
    return directory / APPROVALS_FILE_NAME


def approved_documents(directory: Path) -> dict[str, str]:
    """{nom du fichier: empreinte acceptée}."""

    path = approvals_path(directory)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        logger.error("%s illisible : aucun document partenaire n'est considéré comme validé.", path.name)
        return {}
    return {str(name): str(sha) for name, sha in data.items()} if isinstance(data, Mapping) else {}


def is_approved(path: Path) -> bool:
    return approved_documents(path.parent).get(path.name) == file_sha256(path)


def approve_document(directory: Path, file_name: str, sha256: str) -> tuple[bool, str]:
    """Inscrit un document accepté (nom et empreinte). Renvoie (fait ?, explication)."""

    target = directory / file_name
    if not target.is_file():
        return False, f"{file_name} n'est plus dans {directory} : rien à valider."
    if file_sha256(target) != sha256:
        return False, f"{file_name} a changé depuis la proposition : il sera relu et reproposé."
    approvals = approved_documents(directory)
    approvals[file_name] = sha256
    path = approvals_path(directory)
    path.write_text(json.dumps(dict(sorted(approvals.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, f"{path.name} mis à jour : {file_name} sera importé à la prochaine collecte."


def is_document(path: Path) -> bool:
    return path.suffix.lower() in DOCUMENT_EXTENSIONS
