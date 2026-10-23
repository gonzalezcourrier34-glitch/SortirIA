"""Lecteur de programmes publiés en PDF, structurés par Mistral.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026), sur le
modèle de `src/extractors/ical/ical_extractor.py` : l'orchestration est
assurée par le moteur commun `src/extractors/files/`, ce module ne fait que
lire un PDF. La conversion et les garde-fous sont dans
`src/event/document_mapper.py`.

Pour chaque source de `document_sources` (config/sources.yaml) :

1. téléchargement du PDF et calcul de son empreinte (SHA-256) ;
2. **si le PDF n'a pas changé depuis la dernière lecture, l'extraction en
   cache est réutilisée : Mistral n'est pas rappelé** (coût nul les nuits où
   rien ne change) ;
3. sinon : extraction locale du texte avec pypdf (gratuite, le PDF a une
   couche de texte), découpage par paquets de pages, et pour chaque paquet un
   appel à Mistral qui renvoie les événements en JSON ;
4. validation (Pydantic, titre retrouvé dans la page, date dans la période).

Exemple de source :

    document_sources:
      - name: "Sète agglopôle - Agenda Culture sept.-déc. 2026"
        source_id: "agglopole_agenda_culture"
        type: "document"
        url: "https://www.agglopole.fr/.../Agenda-Culture-...-2026.pdf"
        credit: "Sète agglopôle méditerranée, Agenda Culture"
        published_on: "2026-08-01"
        period: {debut: "2026-09-01", fin: "2026-12-31"}

Essai sans appeler Mistral (texte extrait seulement) :

    uv run python -m src.extractors.documents.document_extractor --texte <texte_du_pdf>.txt
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config.constants import SOURCE_TYPE_DOCUMENT
from config.paths import DATA_DIR
from config.settings import HTTP_HEADERS
from src.event.document_mapper import (
    EXTRACTION_PROMPT,
    PROMPT_VERSION,
    REASON_OUT_OF_PERIOD,
    REASON_TITLE_NOT_IN_TEXT,
    DocumentEvent,
    describe_period,
    expand_dates,
    fix_listed_sessions,
    in_period,
    map_document_event,
    parse_holidays,
    parse_llm_response,
    parse_period,
    title_found_in_text,
    validate_llm_event,
)
from src.event.event_normalizer import PARIS, normalize_for_comparison
from src.event.event_pipeline import occurrence_article_url
from src.extractors.core.extractor_results import ExtractorResult
from src.extractors.files.file_adapter import FileSourceAdapter
from src.extractors.files.file_engine import FileSourceExtractor, extract_file_sources
from src.logger import get_logger
from src.utils.extractor_utils import build_standard_article
from src.utils.parsing_utils import parse_non_negative_integer
from src.utils.value_utils import normalize_value

logger = get_logger(__name__)

CACHE_DIR = DATA_DIR / "cache" / "documents"
DEFAULT_MODEL = "mistral-small-latest"
DEFAULT_PAGES_PER_CHUNK = 3
DEFAULT_DAYS_FORWARD = 30
MAX_CHUNK_CHARS = 15000
# Plan gratuit de Mistral : quelques requêtes par minute seulement. Le
# 14/10/2026, 19 appels envoyés en 6 secondes ont tous reçu une erreur 429.
DEFAULT_CALL_PAUSE_S = 3.0
RATE_LIMIT_ATTEMPTS = 5
RATE_LIMIT_FIRST_WAIT_S = 10.0
REQUEST_TIMEOUT_S = 60.0

# Une fonction (consigne, texte) -> (réponse, consommation de tokens).
LlmCall = Callable[[str, str], tuple[str, dict[str, int]]]

# Tokens consommés depuis le dernier relevé, par modèle : l'agent les relève
# après l'extraction (`take_token_usage`) pour le coût de la nuit.
TOKEN_USAGE: dict[str, dict[str, int]] = {}


def record_token_usage(model: str, usage: Mapping[str, int]) -> None:
    """Ajoute la consommation d'un appel au relevé en cours.

    Parameters
    ----------
    model : str
        Nom du modèle Mistral appelé (clé du relevé).
    usage : Mapping[str, int]
        Consommation de l'appel, avec les clés `input_tokens` et `output_tokens`
        (une clé absente compte pour 0).
    """

    slot = TOKEN_USAGE.setdefault(model, {"input": 0, "output": 0})
    slot["input"] += int(usage.get("input_tokens", 0))
    slot["output"] += int(usage.get("output_tokens", 0))


def take_token_usage() -> dict[str, dict[str, int]]:
    """Rend le relevé depuis le dernier appel, puis le remet à zéro.

    Returns
    -------
    dict[str, dict[str, int]]
        Copie du relevé : pour chaque modèle, les tokens `input` et `output`.
    """

    usage = {model: dict(tokens) for model, tokens in TOKEN_USAGE.items()}
    TOKEN_USAGE.clear()
    return usage


# ---------------------------------------------------------------------------
# Téléchargement et texte
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=8),
    reraise=True,
)
def fetch_document(url: str) -> bytes:
    """Télécharge le PDF (trois essais en cas de coupure réseau).

    Parameters
    ----------
    url : str
        Adresse du PDF, lue dans `config/sources.yaml`.

    Returns
    -------
    bytes
        Contenu du PDF (commence par `%PDF`).

    Raises
    ------
    ValueError
        Si le PDF est introuvable (404) ou si le contenu reçu n'est pas un PDF
        (page web servie à la place, par exemple par une protection anti-robots).
    httpx.HTTPStatusError
        Pour toute autre réponse HTTP en erreur.
    """

    response = httpx.get(url, headers=dict(HTTP_HEADERS), timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
    if response.status_code == 404:
        # Les sites WordPress rangent les fichiers par mois de mise en ligne
        # (`uploads/AAAA/MM/`) : un PDF republié change d'adresse. Le
        # 14/10/2026, l'Agenda Culture est passé de `2026/08` à `2026/09`.
        raise ValueError(
            f"PDF introuvable (404) : {url}. Le document a sans doute été "
            "déplacé ; mettre à jour `url` dans config/sources.yaml."
        )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        # Constat du 15/10/2026 sur Azure : le site de Sète agglopôle sert le
        # PDF à une box, mais une page web aux centres de données. Le message
        # dit ce qui est arrivé, pour que le diagnostic de l'agent le range
        # en « accès refusé » plutôt qu'en « format inattendu ».
        content_type = response.headers.get("content-type", "inconnu")
        if "html" in content_type.lower():
            raise ValueError(
                f"Le document téléchargé n'est pas un PDF : page web reçue ({content_type}, "
                f"HTTP {response.status_code}). Accès bloqué par le site (protection "
                "anti-robots ?) : il sert peut-être le PDF depuis une autre adresse IP."
            )
        raise ValueError(f"Le document téléchargé n'est pas un PDF ({content_type}).")
    return response.content


def extract_pages_text(pdf_bytes: bytes) -> list[str]:
    """Texte de chaque page, extrait localement (gratuit, sans OCR).

    Parameters
    ----------
    pdf_bytes : bytes
        Contenu du PDF.

    Returns
    -------
    list[str]
        Texte de chaque page, dans l'ordre ; chaîne vide pour une page sans texte.
    """

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [(page.extract_text() or "").strip() for page in reader.pages]


def parse_page_selection(selection: Any, page_count: int) -> list[int]:
    """Pages à lire, depuis la configuration (`pages: "4-59,63"`), numérotées à partir de 1.

    Sans sélection : toutes les pages. Le sommaire, l'édito et les pages
    pratiques (horaires d'ouverture) n'apportent pas d'événement mais coûtent
    des tokens et risquent d'être lus comme tels.

    Parameters
    ----------
    selection : Any
        Valeur de `pages` dans la configuration : plages et numéros séparés par
        des virgules (« 4-59,63 »). Vide : toutes les pages.
    page_count : int
        Nombre de pages du PDF ; les numéros hors du document sont ignorés.

    Returns
    -------
    list[int]
        Numéros de pages retenus, triés et sans doublon.
    """

    if not selection:
        return list(range(1, page_count + 1))
    numbers: set[int] = set()
    for part in str(selection).split(","):
        first, _, last = part.strip().partition("-")
        if first.isdigit():
            numbers.update(range(int(first), int(last or first) + 1))
    return sorted(number for number in numbers if 1 <= number <= page_count)


def chunk_pages(
    pages: list[str],
    pages_per_chunk: int,
    selected: list[int] | None = None,
    overlap: int = 0,
) -> list[tuple[int, str]]:
    """Paquets de pages : (numéro de la première page, texte).

    Chaque page du paquet est précédée de « [Page N] » pour que l'on retrouve
    ensuite la page d'un événement. `overlap` pages sont répétées d'un paquet
    au suivant : une liste de séances qui continue sur la page d'après garde
    ainsi son titre (les doublons sont retirés à la lecture).

    Parameters
    ----------
    pages : list[str]
        Texte de chaque page du PDF.
    pages_per_chunk : int
        Nombre de pages par paquet.
    selected : list[int] | None
        Numéros des pages à lire (à partir de 1). Par défaut toutes ; les pages
        sans texte sont toujours écartées.
    overlap : int
        Nombre de pages répétées d'un paquet au suivant. Par défaut 0.

    Returns
    -------
    list[tuple[int, str]]
        Pour chaque paquet, le numéro de sa première page et son texte, tronqué
        à `MAX_CHUNK_CHARS` caractères.
    """

    numbers = [n for n in (selected or range(1, len(pages) + 1)) if pages[n - 1]]
    step = max(pages_per_chunk - overlap, 1)
    chunks = []
    for start in range(0, len(numbers), step):
        group = numbers[start:start + pages_per_chunk]
        chunks.append((group[0], "\n\n".join(f"[Page {n}]\n{pages[n - 1]}" for n in group)[:MAX_CHUNK_CHARS]))
        if start + pages_per_chunk >= len(numbers):
            break
    return chunks


# ---------------------------------------------------------------------------
# Mistral
# ---------------------------------------------------------------------------

def call_mistral(system_prompt: str, text: str) -> tuple[str, dict[str, int]]:
    """Appelle Mistral en mode JSON. Renvoie (réponse, tokens consommés).

    Le modèle est lu dans la variable d'environnement `DOCUMENT_EXTRACTION_MODEL`
    (par défaut `mistral-small-latest`), avec une température nulle.

    Parameters
    ----------
    system_prompt : str
        Consigne d'extraction (message système).
    text : str
        Texte du paquet de pages à lire.

    Returns
    -------
    tuple[str, dict[str, int]]
        La réponse brute (JSON en texte) et la consommation, avec les clés
        `input_tokens` et `output_tokens`.
    """

    from langchain_mistralai import ChatMistralAI

    model = os.getenv("DOCUMENT_EXTRACTION_MODEL", DEFAULT_MODEL)
    llm = ChatMistralAI(model=model, temperature=0).bind(response_format={"type": "json_object"})
    message = llm.invoke([("system", system_prompt), ("human", text)])
    usage = getattr(message, "usage_metadata", None) or {}
    return str(message.content), {
        "input_tokens": int(usage.get("input_tokens", 0)),
        "output_tokens": int(usage.get("output_tokens", 0)),
    }


def is_rate_limited(error: Exception) -> bool:
    """Vrai si Mistral a refusé l'appel pour cause de quota (HTTP 429).

    Parameters
    ----------
    error : Exception
        Erreur levée par l'appel ; seul son message est examiné.

    Returns
    -------
    bool
        Vrai si le message mentionne « 429 » ou « rate limit ».
    """

    return "429" in str(error) or "rate limit" in str(error).lower()


def call_with_rate_limit(
    llm: LlmCall,
    prompt: str,
    text: str,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, dict[str, int]]:
    """Appelle Mistral ; en cas de 429, attend (10 s, 20 s, 40 s...) et réessaie.

    Au plus `RATE_LIMIT_ATTEMPTS` essais ; toute autre erreur remonte aussitôt.

    Parameters
    ----------
    llm : LlmCall
        Fonction d'appel au modèle (consigne, texte) -> (réponse, tokens).
    prompt : str
        Consigne d'extraction.
    text : str
        Texte du paquet de pages.
    sleep : Callable[[float], None]
        Fonction d'attente, remplaçable dans les tests. Par défaut `time.sleep`.

    Returns
    -------
    tuple[str, dict[str, int]]
        La réponse du modèle et sa consommation de tokens.

    Raises
    ------
    Exception
        L'erreur de l'appel si ce n'est pas un quota, ou si le dernier essai
        échoue encore.
    """

    wait = RATE_LIMIT_FIRST_WAIT_S
    for attempt in range(1, RATE_LIMIT_ATTEMPTS + 1):
        try:
            return llm(prompt, text)
        except Exception as error:
            if not is_rate_limited(error) or attempt == RATE_LIMIT_ATTEMPTS:
                raise
            logger.info("Quota Mistral atteint, nouvel essai dans %.0f s (%s/%s).", wait, attempt, RATE_LIMIT_ATTEMPTS)
            sleep(wait)
            wait *= 2
    raise RuntimeError("inaccessible")  # pragma: no cover


# ---------------------------------------------------------------------------
# Lecture d'un document, avec cache
# ---------------------------------------------------------------------------

def cache_path(pdf_bytes: bytes, prompt: str = "", chunking: str = "") -> Path:
    """Fichier de cache : empreinte du PDF, de la consigne et du découpage, plus le modèle.

    La consigne complète (période comprise) et le découpage en paquets (pages
    lues, taille, recouvrement) entrent dans l'empreinte : changer la période
    dans `sources.yaml` ou la consigne sans toucher à `PROMPT_VERSION` ne
    réutilise plus une extraction faite avec d'autres réglages (15/10/2026).

    Parameters
    ----------
    pdf_bytes : bytes
        Contenu du PDF.
    prompt : str
        Consigne complète envoyée à Mistral. Par défaut vide.
    chunking : str
        Description du découpage (pages lues, taille, recouvrement). Par défaut vide.

    Returns
    -------
    Path
        Chemin du fichier JSON de cache dans `CACHE_DIR`.
    """

    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    settings = hashlib.sha256(f"{prompt}\n{chunking}".encode()).hexdigest()[:8]
    model = os.getenv("DOCUMENT_EXTRACTION_MODEL", DEFAULT_MODEL).replace("/", "_")
    return CACHE_DIR / f"{digest}-{PROMPT_VERSION}-{settings}-{model}.json"


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    """Paquets en cache, par première page. Un cache illisible (écriture coupée) est ignoré.

    Parameters
    ----------
    path : Path
        Fichier de cache (voir `cache_path`).

    Returns
    -------
    dict[str, dict[str, Any]]
        Paquets en cache, indexés par le numéro (en texte) de leur première page ;
        vide si le fichier n'existe pas ou est illisible.
    """

    if not path.exists():
        return {}
    try:
        chunks = json.loads(path.read_text(encoding="utf-8"))
        return {str(chunk["first_page"]): chunk for chunk in chunks}
    except (ValueError, KeyError, TypeError) as error:
        logger.warning("Cache %s illisible (%s) : le document sera relu.", path.name, error)
        return {}


def save_cache(path: Path, cached: Mapping[str, Mapping[str, Any]]) -> None:
    """Écrit le cache en entier, de façon atomique (fichier temporaire puis remplacement).

    Parameters
    ----------
    path : Path
        Fichier de cache à écrire ; son dossier est créé au besoin.
    cached : Mapping[str, Mapping[str, Any]]
        Paquets à enregistrer, indexés par première page.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(list(cached.values()), ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def read_document(
    source: Mapping[str, Any],
    pdf_bytes: bytes,
    llm: LlmCall,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Réponses brutes de Mistral par paquet de pages, depuis le cache si possible.

    Chaque élément : {"first_page": n, "text": texte du paquet, "events": [...]}.
    Le cache garde les réponses AVANT validation : si l'on durcit une règle,
    elle s'applique sans rappeler Mistral.

    Seuls les paquets **réussis** vont en cache, et il est écrit après chaque
    paquet : après une coupure ou un quota atteint, le lancement suivant ne
    redemande que les paquets manquants. (Correction du 14/10/2026 : la
    première version mettait aussi en cache les paquets en échec, d'où un
    document « vide » pour toujours.) Si aucun paquet n'a pu être lu, la
    source est déclarée en échec.

    Parameters
    ----------
    source : Mapping[str, Any]
        Entrée de `document_sources` (période, pages, taille des paquets...).
    pdf_bytes : bytes
        Contenu du PDF.
    llm : LlmCall
        Fonction d'appel au modèle (consigne, texte) -> (réponse, tokens).
    sleep : Callable[[float], None]
        Fonction d'attente entre deux appels. Par défaut `time.sleep`.

    Returns
    -------
    list[dict[str, Any]]
        Un élément par paquet lu (en cache ou nouvellement), dans l'ordre des pages.

    Raises
    ------
    RuntimeError
        Si le document a des paquets mais qu'aucun n'a pu être lu.
    """

    # Préparer la consigne (période comprise) et le découpage en paquets
    period = parse_period(source)
    prompt = EXTRACTION_PROMPT.format(periode=describe_period(period))
    pages_per_chunk = max(parse_non_negative_integer(source.get("pages_per_chunk"), DEFAULT_PAGES_PER_CHUNK), 1)
    overlap = min(parse_non_negative_integer(source.get("pages_overlap"), 0), pages_per_chunk - 1)
    pages = extract_pages_text(pdf_bytes)
    selected = parse_page_selection(source.get("pages"), len(pages))
    chunks = chunk_pages(pages, pages_per_chunk, selected, overlap)
    pause = float(os.getenv("DOCUMENT_CALL_PAUSE_S", DEFAULT_CALL_PAUSE_S))
    # Charger le cache correspondant exactement à ces réglages
    path = cache_path(pdf_bytes, prompt, f"{source.get('pages', '')}|{pages_per_chunk}|{overlap}")
    cached = load_cache(path)
    if pages and not chunks:
        logger.warning("%s : aucune page avec du texte (PDF scanné ?) ; rien à lire.", source.get("name"))

    # Lire chaque paquet : depuis le cache, sinon par Mistral (cache écrit à chaque succès)
    results, failures, calls, tokens_in, tokens_out = [], 0, 0, 0, 0
    for first_page, text in chunks:
        if str(first_page) in cached:
            results.append(cached[str(first_page)])
            continue
        if calls:
            sleep(pause)
        calls += 1
        usage: dict[str, int] = {}
        try:
            answer, usage = call_with_rate_limit(llm, prompt, text, sleep)
            events = parse_llm_response(answer)
        except Exception as error:  # noqa: BLE001 (un paquet illisible ne bloque pas les autres)
            logger.warning("%s, pages %s+ : réponse inexploitable (%s).", source.get("name"), first_page, error)
            failures += 1
            continue
        finally:
            # Un appel réussi mais illisible a quand même coûté des tokens.
            if usage:
                tokens_in += usage.get("input_tokens", 0)
                tokens_out += usage.get("output_tokens", 0)
                record_token_usage(os.getenv("DOCUMENT_EXTRACTION_MODEL", DEFAULT_MODEL), usage)
        chunk = {"first_page": first_page, "text": text, "events": events}
        results.append(chunk)
        cached[str(first_page)] = chunk
        save_cache(path, cached)

    # Faire le bilan (appels, échecs, tokens) et échouer si rien n'a été lu
    if calls:
        logger.info(
            "%s : %s paquet(s) envoyés à Mistral (%s en échec), %s en cache ; %s tokens en entrée, %s en sortie.",
            source.get("name"), calls, failures, len(chunks) - calls, tokens_in, tokens_out,
        )
    elif chunks:
        logger.info("%s : PDF inchangé, extraction en cache réutilisée (aucun appel à Mistral).", source.get("name"))
    if chunks and not results:
        raise RuntimeError(f"Aucun paquet de pages n'a pu être lu par Mistral ({failures} échec(s)).")
    return results


def page_of(title: str, chunk: Mapping[str, Any]) -> int:
    """Page où le titre apparaît dans le paquet (sinon la première du paquet).

    Parameters
    ----------
    title : str
        Titre de l'événement.
    chunk : Mapping[str, Any]
        Paquet lu, avec son texte balisé « [Page N] » et sa `first_page`.

    Returns
    -------
    int
        Numéro de la page où le titre est retrouvé.
    """

    for block in str(chunk["text"]).split("[Page ")[1:]:
        number, _, text = block.partition("]")
        if number.isdigit() and title_found_in_text(title, text):
            return int(number)
    return int(chunk["first_page"])


def iter_document_items(source: Mapping[str, Any], llm: LlmCall | None = None) -> list[dict[str, Any]]:
    """Événements validés du document, dans la fenêtre de dates.

    Les événements rejetés par les garde-fous sont renvoyés aussi, avec leur
    motif : le moteur les compte dans les rejets du bilan.

    Parameters
    ----------
    source : Mapping[str, Any]
        Entrée de `document_sources` (url, période, vacances, `days_forward`...).
    llm : LlmCall | None
        Fonction d'appel au modèle. Par défaut `call_mistral`.

    Returns
    -------
    list[dict[str, Any]]
        Éléments `{"event", "page", "document_url"}` pour les séances retenues, et
        `{"rejected", "title"}` pour les événements écartés par les garde-fous.
    """

    # Télécharger et lire le document, puis fixer la fenêtre de collecte
    url = normalize_value(source.get("url"))
    pdf_bytes = fetch_document(url)
    chunks = read_document(source, pdf_bytes, llm or call_mistral)
    period = parse_period(source)
    holidays = parse_holidays(source)
    today = datetime.now(PARIS).date()
    horizon = today + timedelta(days=parse_non_negative_integer(source.get("days_forward"), DEFAULT_DAYS_FORWARD))

    # Valider chaque événement : schéma, titre présent dans la page, période
    items: list[dict[str, Any]] = []
    accepted: list[DocumentEvent] = []
    pages: list[Any] = []
    for chunk in chunks:
        for raw in chunk["events"]:
            event, reason = validate_llm_event(raw)
            if event is None:
                items.append({"rejected": reason})
                continue
            if not title_found_in_text(event.titre, chunk["text"]):
                items.append({"rejected": REASON_TITLE_NOT_IN_TEXT, "title": event.titre})
                continue
            if not in_period(event, period):
                items.append({"rejected": REASON_OUT_OF_PERIOD, "title": event.titre})
                continue
            accepted.append(event)
            pages.append(page_of(event.titre, chunk))

    # Séances listées une à une : la fin de la période recopiée sur chacune est retirée.
    seen: set[tuple[str, date, Any]] = set()
    for event, page in zip(fix_listed_sessions(accepted), pages, strict=True):
        for session in expand_dates(event, today, horizon, holidays):
            if (session.date_fin or session.date_debut) < today or session.date_debut > horizon:
                continue  # hors de la fenêtre de collecte : ni retenu, ni compté comme rejet
            key = (normalize_for_comparison(session.titre), session.date_debut, session.heure_debut)
            if key in seen:
                continue  # déjà lu dans le paquet précédent (page de recouvrement)
            seen.add(key)
            items.append({"event": session, "page": page, "document_url": url})
    return items


def validate_document_item(item: Any, source: Mapping[str, Any]) -> tuple[bool, str]:
    """Les rejets des garde-fous remontent au moteur avec leur motif.

    Parameters
    ----------
    item : Any
        Élément produit par `iter_document_items`.
    source : Mapping[str, Any]
        Source du document (inutilisée, imposée par l'adaptateur).

    Returns
    -------
    tuple[bool, str]
        Vrai et motif vide si l'élément est retenu, sinon faux et le motif du rejet.
    """

    del source
    if not isinstance(item, Mapping):
        return False, "element_document_invalide"
    if item.get("rejected"):
        return False, str(item["rejected"])
    return True, ""


def build_document_article(item: Mapping[str, Any], source: Mapping[str, Any]) -> dict[str, Any]:
    """Transforme un événement validé en « article » du moteur, événement inclus.

    Parameters
    ----------
    item : Mapping[str, Any]
        Élément retenu par `iter_document_items` (événement, page, url du PDF).
    source : Mapping[str, Any]
        Source du document (nom, rôle, crédit...).

    Returns
    -------
    dict[str, Any]
        Article standard ; l'événement normalisé est dans `metadata["event"]`.
    """

    event = map_document_event(
        item["event"], source, page=item["page"], document_url=item["document_url"],
        extracted_at=datetime.now(PARIS).isoformat(),
    )
    return build_standard_article(
        identifier=event.source_event_id,
        source=source.get("name", "Document"),
        title=event.title,
        text=event.description,
        image_url="",
        published_at=event.start_at,
        url=occurrence_article_url(event),
        language="fr",
        category=event.category or "evenement",
        role=source.get("role", "acquisition"),
        metadata={"event": event.to_dict(), "page": item["page"]},
    )


DOCUMENT_ADAPTER = FileSourceAdapter(
    iter_items=iter_document_items,
    build_article=build_document_article,
    validate_item=validate_document_item,
    default_name="Programmes PDF",
)

DOCUMENT_EXTRACTOR = FileSourceExtractor(adapter=DOCUMENT_ADAPTER, source_type=SOURCE_TYPE_DOCUMENT)


def extract_articles_from_sources(sources: list[Mapping[str, Any]]) -> ExtractorResult:
    """Lit une liste de documents déjà chargée.

    Parameters
    ----------
    sources : list[Mapping[str, Any]]
        Entrées de type `document_sources`.

    Returns
    -------
    ExtractorResult
        Articles extraits, statut et bilan des rejets.
    """

    return extract_file_sources(sources, DOCUMENT_ADAPTER, SOURCE_TYPE_DOCUMENT)


def extract_all_articles() -> ExtractorResult:
    """Lit tous les documents de `document_sources`.

    Returns
    -------
    ExtractorResult
        Articles extraits, statut et bilan des rejets.
    """

    return DOCUMENT_EXTRACTOR.run()


def main() -> None:
    """Point d'entrée en ligne de commande.

    Avec `--texte FICHIER`, écrit le texte extrait du premier document sans
    appeler Mistral ; sinon lance l'extraction complète et affiche le bilan.
    """
    parser = argparse.ArgumentParser(description="Programmes PDF lus par Mistral.")
    parser.add_argument("--texte", type=Path, help="N'appelle pas Mistral : écrit le texte extrait du premier document.")
    args = parser.parse_args()

    if args.texte:
        source = DOCUMENT_EXTRACTOR.get_sources()[0]
        pages = extract_pages_text(fetch_document(source["url"]))
        args.texte.write_text("\n\n".join(f"[Page {i + 1}]\n{t}" for i, t in enumerate(pages)), encoding="utf-8")
        print(f"{len(pages)} page(s), {sum(map(len, pages))} caractères -> {args.texte}")
        return
    result = extract_all_articles()
    print(f"{len(result.articles)} événement(s). Statut : {result.status}. Rejets : {result.rejection_reasons}")


if __name__ == "__main__":
    main()
