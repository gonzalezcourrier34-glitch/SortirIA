"""Diagnostic des pannes et propositions de réparation, validées par un humain.

**Provenance** : nouveau module écrit pour SortirIA (14/10/2026). Il part d'un
cas réel du jour même : le PDF de l'Agglopôle a changé d'adresse (dossier
`uploads/2026/08/` devenu `uploads/2026/09/`) et la source est tombée en 404.
Un agent ordinaire écrit « source en échec ». Celui-ci :

1. **classe** la panne d'après son message (document déplacé, accès refusé,
   quota, réseau, format inattendu) et dit quoi faire ;
2. pour un document déplacé, **cherche** la nouvelle adresse (même nom de
   fichier dans les dossiers mensuels voisins, convention WordPress) et la
   **vérifie** (réponse 200, c'est bien un PDF) ;
3. **propose** la réparation, sans l'appliquer : la proposition est écrite
   dans `sortiria.agent_proposals` et dans le mail de bilan ; elle n'est
   appliquée que si un humain l'accepte (`uv run python -m agent.proposals`).

L'agent ne modifie jamais seul sa configuration : une adresse « trouvée »
pourrait être le mauvais document. C'est la règle de sécurité du projet.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Familles de pannes, du message d'erreur à une action conseillée.
KIND_MOVED = "document_deplace"
KIND_ACCESS = "acces_refuse"
KIND_BLOCKED = "acces_bloque"   # page web reçue à la place du document (15/10/2026, Azure)
KIND_QUOTA = "quota"
KIND_NETWORK = "reseau"
KIND_FORMAT = "format_inattendu"
KIND_UNKNOWN = "inconnue"

ADVICE = {
    KIND_MOVED: "le document a changé d'adresse : chercher la nouvelle et mettre à jour `url`.",
    KIND_ACCESS: "accès refusé : vérifier la clé API dans le .env (elle a pu expirer ou être révoquée).",
    KIND_BLOCKED: ("le site a renvoyé une page web au lieu du document (protection anti-robots ?) : "
                   "le lire depuis une autre adresse (le PC) et le couper sur ce serveur (SOURCES_DESACTIVEES)."),
    KIND_QUOTA: "quota atteint : la source sera relue la nuit prochaine ; si cela dure, espacer les appels.",
    KIND_NETWORK: "site injoignable ou trop lent : en général passager, à surveiller s'il se répète.",
    KIND_FORMAT: "réponse inattendue : la source a peut-être changé de format, vérifier l'adaptateur.",
    KIND_UNKNOWN: "panne non reconnue : voir le journal (logs/pipeline.log).",
}

PATTERNS = (
    (KIND_BLOCKED, re.compile(r"page web re[çc]ue|acc[èe]s bloqu", re.IGNORECASE)),
    (KIND_MOVED, re.compile(r"404|introuvable|not found", re.IGNORECASE)),
    (KIND_ACCESS, re.compile(r"\b40[13]\b|unauthori[sz]ed|forbidden|cl[ée] api|authentication", re.IGNORECASE)),
    (KIND_QUOTA, re.compile(r"429|rate limit|quota", re.IGNORECASE)),
    (KIND_NETWORK, re.compile(r"timeout|délai dépassé|connect|réseau|network|50[234]", re.IGNORECASE)),
    (KIND_FORMAT, re.compile(r"json|pars|format|keyerror|schema|n'est pas un pdf", re.IGNORECASE)),
)
URL_PATTERN = re.compile(r"https?://[^\s)\"']+?(?=[.,;]?(?:\s|$))")
WORDPRESS_UPLOAD = re.compile(r"^(?P<base>https?://.+/uploads/)(?P<year>\d{4})/(?P<month>\d{2})/(?P<file>[^/]+)$")


@dataclass(slots=True)
class Diagnosis:
    """Une panne comprise : où, quoi, que faire, et éventuellement une réparation proposée.

    Attributes
    ----------
    source_name : str
        Nom de la source en panne, tel qu'il apparaît dans le bilan des extracteurs.
    kind : str
        Famille de la panne (une des constantes `KIND_...`).
    message : str
        Message d'erreur d'origine.
    advice : str
        Action conseillée pour cette famille de panne (tirée de `ADVICE`).
    proposal : dict[str, Any]
        Réparation proposée, à valider par un humain (`type`, `source_id`, `old`,
        `new`) ; vide si aucune.
    """

    source_name: str
    kind: str
    message: str
    advice: str
    proposal: dict[str, Any] = field(default_factory=dict)   # {"type", "source_id", "old", "new"}

    def as_alert(self) -> str:
        """Rédiger l'alerte d'une ligne qui figure dans le bilan de la nuit.

        Returns
        -------
        str
            Le diagnostic et le conseil, suivis de la nouvelle adresse proposée s'il y en a une.
        """
        text = f"Diagnostic {self.source_name} ({self.kind}) : {self.advice}"
        if self.proposal:
            text += f" Proposition : remplacer l'adresse par {self.proposal['new']} (à valider)."
        return text


def classify(message: str) -> str:
    """Famille de panne d'après le message d'erreur.

    Les motifs de `PATTERNS` sont essayés dans l'ordre : le premier qui
    correspond l'emporte.

    Parameters
    ----------
    message : str
        Message d'erreur de l'extracteur (peut être vide).

    Returns
    -------
    str
        La famille de panne (`KIND_...`), `KIND_UNKNOWN` si aucun motif ne correspond.
    """

    for kind, pattern in PATTERNS:
        if pattern.search(message or ""):
            return kind
    return KIND_UNKNOWN


def failures(extractor_stats: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """(source, message) de chaque échec : extracteur en échec, ou source en échec dans un lot.

    Les messages d'un lot ont la forme « Nom de la source : message ».

    Parameters
    ----------
    extractor_stats : Iterable[Mapping[str, Any]]
        Bilan de chaque extracteur (`name`, `status`, `message`, `errors`).

    Returns
    -------
    list[tuple[str, str]]
        Un couple (nom de la source, message d'erreur) par échec.
    """

    found = []
    for stat in extractor_stats:
        errors = [str(error) for error in stat.get("errors") or []]
        if not errors and stat.get("status") == "failed":
            errors = [f"{stat.get('name', '?')} : {stat.get('message', '')}"]
        for error in errors:
            name, _, message = error.partition(" : ")
            found.append((name.strip() if message else str(stat.get("name", "?")), message or error))
    return found


def candidate_urls(url: str, around: date, months_back: int = 6, months_forward: int = 2) -> list[str]:
    """Adresses à essayer pour un fichier WordPress déplacé : même nom, mois voisins.

    « .../uploads/2026/08/agenda.pdf » -> « .../uploads/2026/09/agenda.pdf »,
    « .../2026/07/... », etc., du plus proche au plus lointain.

    Parameters
    ----------
    url : str
        Ancienne adresse du document, de la forme `.../uploads/AAAA/MM/fichier`.
    around : date
        Date autour de laquelle chercher (en général aujourd'hui).
    months_back : int
        Nombre de mois à remonter. Par défaut 6.
    months_forward : int
        Nombre de mois à avancer. Par défaut 2.

    Returns
    -------
    list[str]
        Les adresses candidates, sans l'adresse d'origine ; vide si l'adresse
        n'a pas la forme WordPress.
    """

    match = WORDPRESS_UPLOAD.match(url)
    if not match:
        return []
    candidates = []
    for offset in sorted(range(-months_back, months_forward + 1), key=lambda value: (abs(value), -value)):
        month_index = around.year * 12 + around.month - 1 + offset
        year, month = divmod(month_index, 12)
        candidate = f"{match['base']}{year}/{month + 1:02d}/{match['file']}"
        if candidate != url:
            candidates.append(candidate)
    return candidates


def find_moved_document(url: str, probe: Callable[[str], bool], today: date) -> str:
    """Nouvelle adresse vérifiée d'un document déplacé (chaîne vide si rien trouvé).

    Parameters
    ----------
    url : str
        Ancienne adresse du document.
    probe : Callable[[str], bool]
        Vérifie qu'une adresse répond bien avec le document (voir `probe_pdf`).
    today : date
        Date autour de laquelle chercher les dossiers mensuels.

    Returns
    -------
    str
        La première adresse candidate validée par `probe`, ou une chaîne vide.
    """

    for candidate in candidate_urls(url, today):
        if probe(candidate):
            return candidate
    return ""


def diagnose(
    extractor_stats: Iterable[Mapping[str, Any]],
    configured_sources: Iterable[Mapping[str, Any]],
    probe: Callable[[str], bool] | None,
    today: date,
) -> list[Diagnosis]:
    """Diagnostic de chaque échec de la nuit, avec une réparation proposée quand c'est possible.

    Seuls les documents déplacés reçoivent une proposition (nouvelle adresse) ;
    elle n'est jamais appliquée ici, un humain doit l'accepter.

    Parameters
    ----------
    extractor_stats : Iterable[Mapping[str, Any]]
        Bilan de chaque extracteur pour la nuit.
    configured_sources : Iterable[Mapping[str, Any]]
        Sources configurées (`name`, `url`, `source_id`), pour retrouver
        l'adresse et l'identifiant de la source en panne.
    probe : Callable[[str], bool] | None
        Vérifie une adresse candidate ; `None` pour ne rien chercher.
    today : date
        Date du jour, point de départ de la recherche des dossiers mensuels.

    Returns
    -------
    list[Diagnosis]
        Un diagnostic par échec, dans l'ordre des échecs.
    """

    sources = list(configured_sources)
    diagnoses = []
    for name, message in failures(extractor_stats):
        # Classer la panne et joindre le conseil de sa famille
        kind = classify(message)
        diagnosis = Diagnosis(source_name=name, kind=kind, message=message, advice=ADVICE[kind])
        # Document déplacé : retrouver la source, puis chercher et vérifier la nouvelle adresse
        if kind == KIND_MOVED and probe is not None:
            urls = URL_PATTERN.findall(message)
            source = next((s for s in sources if s.get("name") == name), None) or next(
                (s for s in sources if s.get("url") in urls), None)
            old_url = (source or {}).get("url") or (urls[0] if urls else "")
            new_url = find_moved_document(old_url, probe, today) if old_url else ""
            if new_url:
                diagnosis.proposal = {"type": "remplacer_url", "source_id": (source or {}).get("source_id", ""),
                                      "old": old_url, "new": new_url}
        diagnoses.append(diagnosis)
    return diagnoses


def probe_pdf(url: str) -> bool:
    """Vrai si l'adresse répond 200 avec un PDF (requête HEAD, puis début du fichier).

    Si le serveur refuse HEAD (405), on lit les 5 premiers octets et on cherche
    la signature `%PDF`. Toute erreur réseau vaut « non ».

    Parameters
    ----------
    url : str
        Adresse à vérifier.

    Returns
    -------
    bool
        Vrai si l'adresse répond avec un PDF.
    """

    import httpx

    from config.settings import HTTP_HEADERS

    try:
        response = httpx.head(url, headers=dict(HTTP_HEADERS), timeout=10, follow_redirects=True)
        if response.status_code == 405:   # HEAD refusé : on lit seulement le début du fichier
            response = httpx.get(url, headers={**dict(HTTP_HEADERS), "Range": "bytes=0-4"}, timeout=10,
                                 follow_redirects=True)
            return response.status_code in (200, 206) and response.content.startswith(b"%PDF")
        return response.status_code == 200 and "pdf" in response.headers.get("content-type", "").lower()
    except httpx.HTTPError:
        return False
