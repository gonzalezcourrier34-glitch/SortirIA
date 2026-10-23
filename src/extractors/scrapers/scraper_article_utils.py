"""Extraction et construction des articles issus des scrapers CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `src/extractors/scrapers/scraper_article_utils.py` (commit 93632c5).
Modifié pour SortirIA : `extract_label` lit la catégorie de l'événement au lieu d'un verdict de fact-checking.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from bs4 import BeautifulSoup, Tag

from src.extractors.scrapers.scraper_metadata_utils import get_article_json_ld, get_open_graph
from src.extractors.scrapers.scraper_selector_utils import (
    clean_text,
    get_source_selector,
    select_attribute,
    select_element,
    select_text,
)
from src.extractors.scrapers.scraper_url_utils import make_absolute_url
from src.utils.date_utils import convert_date_to_iso
from src.utils.extractor_utils import build_standard_article
from src.utils.value_utils import normalize_value

# Extraction des valeurs structurées

def extract_person_name(value: Any) -> str:
    """Extrait un ou plusieurs noms depuis une valeur auteur JSON-LD.

    Accepte une chaîne, un objet (`name`, `legalName` ou `alternateName`) ou une
    liste de ces valeurs ; dans ce dernier cas, les noms sont dédoublonnés en
    gardant l'ordre et joints par des virgules.

    Parameters
    ----------
    value : Any
        Valeur du champ `author` (ou équivalent) lue dans le JSON-LD.

    Returns
    -------
    str
        Le ou les noms nettoyés, ou une chaîne vide si rien n'est exploitable.
    """

    if isinstance(value, str):
        return clean_text(value)

    if isinstance(value, Mapping):
        return clean_text(
            value.get("name")
            or value.get("legalName")
            or value.get("alternateName")
        )

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray)
    ):
        names = [
            name
            for item in value
            if (name := extract_person_name(item))
        ]
        return ", ".join(dict.fromkeys(names))

    return ""


def extract_json_ld_image(value: Any) -> str:
    """Extrait une URL d'image depuis une valeur JSON-LD.

    Accepte une chaîne, un objet `ImageObject` (`url`, `contentUrl` ou `@id`)
    ou une liste ; pour une liste, la première URL trouvée est retenue.

    Parameters
    ----------
    value : Any
        Valeur du champ `image` lue dans le JSON-LD.

    Returns
    -------
    str
        L'URL de l'image telle que déclarée, ou une chaîne vide.
    """

    if isinstance(value, str):
        return normalize_value(value)

    if isinstance(value, Mapping):
        return normalize_value(
            value.get("url")
            or value.get("contentUrl")
            or value.get("@id")
        )

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray)
    ):
        for item in value:
            image_url = extract_json_ld_image(item)

            if image_url:
                return image_url

    return ""


def extract_srcset_url(value: Any) -> str:
    """Sélectionne la plus grande URL déclarée dans un attribut srcset.

    Les descripteurs de largeur (`800w`) sont pris tels quels, les densités
    (`2x`) sont multipliées par 1000 pour passer devant ; une entrée sans
    descripteur, ou illisible, compte pour 1.

    Parameters
    ----------
    value : Any
        Contenu brut de l'attribut `srcset` (ou `data-srcset`).

    Returns
    -------
    str
        L'URL de plus grand poids, ou une chaîne vide si l'attribut est vide.
    """

    srcset = normalize_value(value)

    if not srcset:
        return ""

    candidates: list[tuple[float, str]] = []

    # Lire chaque candidat « url descripteur » et lui donner un poids
    for item in srcset.split(","):
        parts = item.strip().split()

        if not parts:
            continue

        url = normalize_value(parts[0])

        if not url:
            continue

        weight = 1.0

        if len(parts) > 1:
            descriptor = parts[1].lower()

            try:
                if descriptor.endswith("w"):
                    weight = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    weight = float(descriptor[:-1]) * 1_000
            except ValueError:
                weight = 1.0

        candidates.append((weight, url))

    # Garder le candidat le plus lourd
    return max(candidates, default=(0.0, ""))[1]


def get_image_url_from_tag(element: Tag | None, page_url: str) -> str:
    """Extrait et normalise l'URL d'une balise image.

    Essaie dans l'ordre `src`, les attributs de chargement différé
    (`data-src`, `data-original`, `data-lazy-src`) puis `srcset` et
    `data-srcset`.

    Parameters
    ----------
    element : Tag | None
        Balise image trouvée dans la page, ou `None`.
    page_url : str
        URL de la page, pour rendre absolue une URL relative.

    Returns
    -------
    str
        L'URL absolue de l'image, ou une chaîne vide.
    """

    if element is None:
        return ""

    raw_url = (
        normalize_value(element.get("src"))
        or normalize_value(element.get("data-src"))
        or normalize_value(element.get("data-original"))
        or normalize_value(element.get("data-lazy-src"))
        or extract_srcset_url(element.get("srcset"))
        or extract_srcset_url(element.get("data-srcset"))
    )

    return make_absolute_url(raw_url, page_url)


# Extraction des champs d'article

def extract_canonical_url(
    soup: BeautifulSoup,
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any],
    page_url: str
) -> str:
    """Extrait l'URL canonique de l'article.

    Ordre de priorité : `<link rel="canonical">`, `og:url`, l'URL du JSON-LD
    (`url` ou `mainEntityOfPage`), puis l'URL de la page elle-même.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article (peut être vide).
    open_graph : Mapping[str, Any]
        Métadonnées Open Graph de la page (peut être vide).
    page_url : str
        URL à laquelle la page a été téléchargée.

    Returns
    -------
    str
        L'URL canonique, absolue et canonisée.
    """

    # Lire les trois sources possibles : balise link, JSON-LD, Open Graph
    configured_url = select_attribute(
        soup,
        "link[rel='canonical']",
        ("href",)
    )
    main_entity = article_json_ld.get("mainEntityOfPage")
    json_ld_url = normalize_value(
        article_json_ld.get("url")
        or main_entity
    )

    if isinstance(main_entity, Mapping):
        json_ld_url = normalize_value(
            main_entity.get("@id")
            or main_entity.get("url")
        )

    # Retenir la première trouvée et la rendre absolue
    raw_url = (
        configured_url
        or normalize_value(open_graph.get("url"))
        or json_ld_url
        or page_url
    )

    return make_absolute_url(
        raw_url,
        page_url,
        canonicalize=True
    )


def extract_title(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any]
) -> str:
    """Extrait le titre de l'article.

    Ordre de priorité : sélecteur de la source, `headline` puis `name` du
    JSON-LD, `og:title`, premier `<h1>`, puis `<title>`.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source (sélecteurs CSS).
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article.
    open_graph : Mapping[str, Any]
        Métadonnées Open Graph.

    Returns
    -------
    str
        Le titre nettoyé, ou une chaîne vide.
    """

    selector = get_source_selector(source, "title")

    return (
        select_text(soup, selector)
        or clean_text(article_json_ld.get("headline"))
        or clean_text(article_json_ld.get("name"))
        or clean_text(open_graph.get("title"))
        or select_text(soup, "h1")
        or select_text(soup, "title")
    )


def extract_text(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    article_json_ld: Mapping[str, Any]
) -> str:
    """Extrait le contenu principal de l'article.

    Essaie d'abord le bloc désigné par le sélecteur `content` de la source
    (paragraphes, sinon tout le texte), puis `articleBody` du JSON-LD, puis les
    paragraphes de la balise `<article>`.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée ; les éléments parasites du bloc retenu en sont
        retirés (`decompose`).
    source : Mapping[str, Any]
        Configuration de la source (sélecteurs CSS).
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article.

    Returns
    -------
    str
        Le texte, paragraphes séparés par une ligne vide, ou une chaîne vide.
    """

    selector = get_source_selector(source, "content")
    element = select_element(soup, selector)

    # Bloc configuré : retirer scripts et navigation, puis garder les paragraphes
    if element is not None:
        for unwanted in element.select(
            "script,style,noscript,iframe,form,nav,aside,footer,button,svg"
        ):
            unwanted.decompose()

        paragraphs = [
            clean_text(node.get_text(separator=" ", strip=True))
            for node in element.select("p")
        ]
        paragraphs = [
            paragraph
            for paragraph in paragraphs
            if paragraph
        ]

        if paragraphs:
            return "\n\n".join(paragraphs)

        text = clean_text(
            element.get_text(separator="\n", strip=True)
        )

        if text:
            return text

    # Sinon, le corps déclaré dans le JSON-LD
    article_body = clean_text(
        article_json_ld.get("articleBody")
    )

    if article_body:
        return article_body

    # En dernier recours, les paragraphes de la balise <article>
    article_element = select_element(soup, "article")

    if article_element is not None:
        paragraphs = [
            clean_text(node.get_text(separator=" ", strip=True))
            for node in article_element.select("p")
        ]
        paragraphs = [
            paragraph
            for paragraph in paragraphs
            if paragraph
        ]

        if paragraphs:
            return "\n\n".join(paragraphs)

    return ""


def extract_label(
    soup: BeautifulSoup,
    source: Mapping[str, Any]
) -> str:
    """Extrait l'étiquette déclarée par la source (par exemple la catégorie).

    Pour SortirIA, l'étiquette sert à récupérer la catégorie d'un événement
    (concert, théâtre...) quand la page la fournit. Le sélecteur CSS vient de
    config/sources.yaml (clé "label" ou "category").

    Modifié pour SortirIA : CheckIt.AI extrayait ici un verdict de
    fact-checking (vrai / faux) ; cette logique a été retirée.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source (sélecteurs `label` ou `category`).

    Returns
    -------
    str
        Le texte trouvé, ou une chaîne vide si aucun sélecteur n'est configuré.
    """

    selector = (
        get_source_selector(source, "label")
        or get_source_selector(source, "category")
    )

    if not selector:
        return ""

    return select_text(soup, selector) or ""


def extract_image_url(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any],
    page_url: str
) -> str:
    """Extrait l'URL principale de l'image.

    Ordre de priorité : sélecteur `image` de la source, `og:image`, `image` du
    JSON-LD, puis la première image de `<article>` ou `<main>`.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source (sélecteurs CSS).
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article.
    open_graph : Mapping[str, Any]
        Métadonnées Open Graph.
    page_url : str
        URL de référence pour rendre les liens absolus.

    Returns
    -------
    str
        L'URL absolue de l'image, ou une chaîne vide.
    """

    # Rassembler les candidats : sélecteur configuré, Open Graph, JSON-LD, repli
    selector = get_source_selector(source, "image")
    configured_element = select_element(soup, selector)
    configured_url = get_image_url_from_tag(
        configured_element,
        page_url
    )

    open_graph_image = normalize_value(
        open_graph.get("image")
        or open_graph.get("image:url")
        or open_graph.get("image:secure_url")
    )
    json_ld_image = extract_json_ld_image(
        article_json_ld.get("image")
    )
    fallback_element = select_element(
        soup,
        "article img, main img"
    )

    # Retenir le premier candidat non vide
    raw_url = (
        configured_url
        or open_graph_image
        or json_ld_image
        or get_image_url_from_tag(
            fallback_element,
            page_url
        )
    )

    return make_absolute_url(raw_url, page_url)


def extract_author(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any]
) -> str:
    """Extrait l'auteur de l'article.

    Ordre de priorité : sélecteur `author` de la source, `author` du JSON-LD,
    `article:author` d'Open Graph, puis `<meta name="author">`.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source (sélecteurs CSS).
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article.
    open_graph : Mapping[str, Any]
        Métadonnées Open Graph.

    Returns
    -------
    str
        Le ou les auteurs, ou une chaîne vide.
    """

    selector = get_source_selector(source, "author")

    return (
        select_text(soup, selector)
        or extract_person_name(article_json_ld.get("author"))
        or clean_text(open_graph.get("article:author"))
        or select_attribute(
            soup,
            "meta[name='author']",
            ("content",)
        )
    )


def extract_published_at(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any]
) -> str:
    """Extrait et normalise la date de publication.

    Ordre de priorité : attribut `datetime`/`content` puis texte du sélecteur
    `published_at`, `datePublished` du JSON-LD, `article:published_time`
    d'Open Graph, puis les balises meta et `<time>` usuelles.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source (sélecteurs CSS).
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article.
    open_graph : Mapping[str, Any]
        Métadonnées Open Graph.

    Returns
    -------
    str
        La date au format ISO, ou ce que renvoie `convert_date_to_iso` pour une
        valeur vide ou illisible.
    """

    selector = get_source_selector(source, "published_at")

    raw_date = (
        select_attribute(
            soup,
            selector,
            ("datetime", "content")
        )
        or select_text(soup, selector)
        or normalize_value(article_json_ld.get("datePublished"))
        or normalize_value(open_graph.get("article:published_time"))
        or select_attribute(
            soup,
            (
                "meta[property='article:published_time'],"
                "meta[name='date'],"
                "meta[name='publish-date'],"
                "time[datetime]"
            ),
            ("content", "datetime")
        )
    )

    return convert_date_to_iso(raw_date)


def extract_language(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any]
) -> str:
    """Extrait la langue de l'article.

    Ordre de priorité : `inLanguage` du JSON-LD, `og:locale`, attribut `lang`
    de `<html>`, puis la langue déclarée pour la source.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source (clé `language`).
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article.
    open_graph : Mapping[str, Any]
        Métadonnées Open Graph.

    Returns
    -------
    str
        Le code de langue court en minuscules (`fr` pour `fr_FR`), ou une
        chaîne vide.
    """

    html_tag = soup.find("html")
    html_language = (
        normalize_value(html_tag.get("lang"))
        if isinstance(html_tag, Tag)
        else ""
    )

    language = (
        normalize_value(article_json_ld.get("inLanguage"))
        or normalize_value(open_graph.get("locale"))
        or html_language
        or normalize_value(source.get("language"))
    )

    if not language:
        return ""

    return (
        language
        .replace("_", "-")
        .split("-", 1)[0]
        .strip()
        .lower()
    )


def normalize_category_value(value: Any) -> str:
    """Normalise une catégorie issue du HTML ou du JSON-LD.

    Accepte une chaîne, un objet (`name`, `headline` ou `@id`) ou une liste ;
    pour une liste, les catégories sont dédoublonnées et jointes par des
    virgules.

    Parameters
    ----------
    value : Any
        Valeur brute de la catégorie.

    Returns
    -------
    str
        La catégorie nettoyée, ou une chaîne vide.
    """

    if isinstance(value, str):
        return clean_text(value)

    if isinstance(value, Mapping):
        return clean_text(
            value.get("name")
            or value.get("headline")
            or value.get("@id")
        )

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray)
    ):
        categories = [
            category
            for item in value
            if (category := normalize_category_value(item))
        ]
        return ", ".join(dict.fromkeys(categories))

    return ""


def extract_category(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    article_json_ld: Mapping[str, Any],
    open_graph: Mapping[str, Any]
) -> str:
    """Extrait la catégorie principale.

    Ordre de priorité : sélecteur `category` de la source, `articleSection` du
    JSON-LD, `article:section` d'Open Graph, puis la catégorie déclarée pour la
    source.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source.
    article_json_ld : Mapping[str, Any]
        Objet JSON-LD de l'article.
    open_graph : Mapping[str, Any]
        Métadonnées Open Graph.

    Returns
    -------
    str
        La catégorie, ou une chaîne vide.
    """

    selector = get_source_selector(source, "category")

    return (
        select_text(soup, selector)
        or normalize_category_value(
            article_json_ld.get("articleSection")
        )
        or clean_text(open_graph.get("article:section"))
        or normalize_category_value(source.get("category"))
    )


# Construction finale

def build_scraped_article(
    soup: BeautifulSoup,
    source: Mapping[str, Any],
    metadata: Mapping[str, Any],
    page_url: str
) -> dict[str, Any]:
    """Construit un article standard depuis une page HTML.

    Chaque champ est extrait par la fonction dédiée de ce module, puis le tout
    est assemblé par `build_standard_article`.

    Parameters
    ----------
    soup : BeautifulSoup
        Page HTML analysée.
    source : Mapping[str, Any]
        Configuration de la source (nom, sélecteurs, rôle, pays...).
    metadata : Mapping[str, Any]
        Métadonnées structurées de la page (JSON-LD, Open Graph...).
    page_url : str
        URL à laquelle la page a été téléchargée.

    Returns
    -------
    dict[str, Any]
        L'article au format standard du projet.
    """

    # Isoler le JSON-LD de l'article et l'Open Graph, puis fixer l'URL canonique
    article_json_ld = get_article_json_ld(metadata)
    open_graph = get_open_graph(metadata)
    canonical_url = extract_canonical_url(
        soup,
        article_json_ld,
        open_graph,
        page_url
    )

    # Extraire chaque champ et assembler l'article standard
    return build_standard_article(
        identifier=canonical_url or page_url,
        source=normalize_value(
            source.get("name")
            or source.get("source")
            or source.get("source_id")
        ),
        title=extract_title(
            soup,
            source,
            article_json_ld,
            open_graph
        ),
        text=extract_text(
            soup,
            source,
            article_json_ld
        ),
        label=extract_label(
            soup,
            source
        ),
        image_url=extract_image_url(
            soup,
            source,
            article_json_ld,
            open_graph,
            canonical_url or page_url
        ),
        published_at=extract_published_at(
            soup,
            source,
            article_json_ld,
            open_graph
        ),
        author=extract_author(
            soup,
            source,
            article_json_ld,
            open_graph
        ),
        language=extract_language(
            soup,
            source,
            article_json_ld,
            open_graph
        ),
        url=canonical_url or page_url,
        category=extract_category(
            soup,
            source,
            article_json_ld,
            open_graph
        ),
        role=normalize_value(source.get("role")),
        metadata={
            "source_id": normalize_value(source.get("source_id")),
            "source_type": normalize_value(source.get("type")) or "scraper",
            "country": normalize_value(source.get("country"))
        }
    )