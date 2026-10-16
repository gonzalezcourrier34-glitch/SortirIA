"""Tests de la source « fichiers des partenaires » à travers le moteur datasets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import src.extractors.datasets.source.partner_extractor as module
from config.constants import EXTRACTOR_STATUS_SUCCESS
from src.event.event_pipeline import event_from_article
from src.extractors.datasets.dataset_engine import extract_dataset_from_source

CSV = """titre;date;heure_debut;lieu;commune;prix;public;lien
Atelier jeux;21/11/2026;14h30;Ludothèque du Port;Sète;3 €;famille;
Atelier jeux;28/11/2026;14h30;Ludothèque du Port;Sète;3 €;famille;
Sans date;;14h30;Ludothèque du Port;Sète;;;
Vieux;01/09/2026;10:00;Ludothèque du Port;Sète;;;
;;;;;;;
Concert;05/12/2026;le soir;Ludothèque du Port;Sète;;;
"""


def make_source(directory: Path, **changes: Any) -> dict[str, Any]:
    return {
        "name": "Fichiers des partenaires", "source_id": "partenaire", "type": "dataset", "enabled": True,
        "path": str(directory), "format": "tableur", "chunksize": 1000, "max_articles": 100, "role": "acquisition",
        "filters": {"require_url": True, "min_title_length": 3, "min_text_length": 0, "min_total_text_length": 3},
        "partners": [{"file": "ludo", "name": "Ludothèque du Port", "sector": "enfants",
                      "website": "https://ludo.example", "sponsored_until": "2026-12-31"}],
        **changes,
    }


@pytest.fixture
def partner_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from datetime import datetime

    from src.event.event_normalizer import PARIS

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 14, 12, 0, tzinfo=tz or PARIS)

    monkeypatch.setattr(module, "datetime", FixedDatetime)
    (tmp_path / "ludo.csv").write_text(CSV, encoding="utf-8-sig")
    (tmp_path / "~$ludo.xlsx").write_text("verrou Excel", encoding="utf-8")
    (tmp_path / "_brouillon.csv").write_text(CSV, encoding="utf-8")
    (tmp_path / "_modeles").mkdir()
    (tmp_path / "_modeles" / "modele.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


def test_engine_end_to_end(partner_dir: Path) -> None:
    result = extract_dataset_from_source(make_source(partner_dir), module.PARTNER_ADAPTER)
    assert result.status == EXTRACTOR_STATUS_SUCCESS
    events = [event_from_article(article) for article in result.articles]
    assert [(e.title, e.start_at[:10], e.is_sponsored) for e in events] == [
        ("Atelier jeux", "2026-11-21", True), ("Atelier jeux", "2026-11-28", True),
    ]
    assert result.rejection_reasons == {"date_illisible": 1, "heure_illisible": 1}
    info = events[0].partner_info
    assert (info["name"], info["file_name"], info["rows_total"], info["rows_loaded"], info["rows_rejected"]) == (
        "Ludothèque du Port", "ludo.csv", 5, 2, 2)
    assert len(info["file_sha256"]) == 64


def test_excel_file_with_real_dates(partner_dir: Path) -> None:
    from datetime import datetime

    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Titre", "Date", "Heure début", "Lieu", "Ville", "Lien"])
    sheet.append(["Soirée DJ", datetime(2026, 11, 28), "23:00",  # noqa: DTZ001 (Excel ne stocke pas de fuseau)
                   "Club du Port", "Sète", "https://club.example"])
    workbook.save(partner_dir / "club_du_port.xlsx")
    items = module.read_partner_file(partner_dir / "club_du_port.xlsx", {"file": "club_du_port"})
    assert items[0]["event"].start_at == "2026-11-28T23:00:00+01:00"
    assert items[0]["event"].audiences == ["soiree"]


def test_missing_columns_reject_the_file(partner_dir: Path) -> None:
    (partner_dir / "ludo.csv").write_text("nom;jour\nAtelier;21/11/2026\n", encoding="utf-8")
    items = module.read_partner_file(partner_dir / "ludo.csv", {"file": "ludo"})
    assert items == [{"rejected": "colonnes_manquantes", "line": 1, "detail": "lieu, commune"}]


def test_every_file_at_the_root_is_a_partner(partner_dir: Path) -> None:
    """Racine seulement : ni verrou Excel, ni brouillon « _ », ni sous-dossier."""

    (partner_dir / "bartrucmuche.json").write_text("[]", encoding="utf-8")
    (partner_dir / "notes.txt").write_text("à lire", encoding="utf-8")
    files = module.find_partner_files(partner_dir, make_source(partner_dir))
    assert [path.name for path in files] == ["bartrucmuche.json", "ludo.csv"]
    disabled = make_source(partner_dir, partners=[{"file": "ludo", "enabled": False}])
    assert [path.name for path in module.find_partner_files(partner_dir, disabled)] == ["bartrucmuche.json"]
    assert module.has_partner_files(make_source(partner_dir))
    assert not module.has_partner_files(make_source(partner_dir / "absent"))


def test_partner_name_comes_from_the_file_name(partner_dir: Path) -> None:
    source = make_source(partner_dir)
    assert module.partner_for_file(Path("saint-louis.xlsx"), source)["name"] == "Saint Louis"
    assert module.partner_for_file(Path("bar_truc_muche.csv"), source)["sector"] == "autre"
    declared = module.partner_for_file(Path("LUDO.csv"), source)   # rapprochement sans casse
    assert (declared["name"], declared["sector"]) == ("Ludothèque du Port", "enfants")
    assert module.partner_for_file(Path("x.csv"), {"partners": [{"file": "x", "sector": "Bowling"}]})["sector"] == "autre"


def test_json_file(partner_dir: Path) -> None:
    (partner_dir / "festival.json").write_text(
        '{"evenements": [{"Titre": "Bal folk", "date": "2026-11-15", "heure_debut": "21:00",'
        ' "lieu": "Scène du port", "Ville": "Marseillan", "lien": "www.festival.example/bal"}]}',
        encoding="utf-8",
    )
    items = module.read_partner_file(partner_dir / "festival.json", {"file": "festival"})
    event = items[0]["event"]
    assert (event.title, event.city, event.url) == ("Bal folk", "Marseillan", "https://www.festival.example/bal")


def test_check_file_report(partner_dir: Path) -> None:
    report = module.check_file(partner_dir / "ludo.csv", make_source(partner_dir)["partners"][0])
    assert "2 séance(s) à venir prête(s), 2 ligne(s) à corriger" in report
    assert "ligne 4 : date_illisible" in report and "ligne 7 : heure_illisible" in report


def test_template_matches_reader(tmp_path: Path) -> None:
    """Le modèle envoyé aux partenaires est lu sans colonne manquante."""

    pytest.importorskip("openpyxl")
    from scripts.creer_modele_partenaire import write_csv, write_excel

    write_excel(tmp_path / "modele.xlsx")
    write_csv(tmp_path / "modele.csv")
    for name in ("modele.xlsx", "modele.csv"):
        headers, rows = module.read_table(tmp_path / name)
        assert not module.missing_columns(headers) and rows == []


# --- Revue du 15/10/2026 : encodage Windows et fichier illisible ------------
def test_windows_csv_is_read(partner_dir: Path) -> None:
    """Excel français enregistre souvent en Windows-1252 : « Soirée » doit rester lisible."""

    windows_csv = "titre;date;heure_debut;lieu;commune;prix;public;lien\nSoirée salsa;05/12/2026;20h30;Le Bar;Sète;;soiree;\n"
    (partner_dir / "bar.csv").write_bytes(windows_csv.encode("cp1252"))
    headers, rows = module.read_table(partner_dir / "bar.csv")
    assert "titre" in headers and rows[0]["titre"] == "Soirée salsa"


def test_unreadable_file_does_not_stop_the_others(partner_dir: Path) -> None:
    """Un tableur corrompu est refusé avec son motif ; les fichiers suivants sont lus."""

    (partner_dir / "aaa_casse.xlsx").write_bytes(b"pas un classeur Excel")
    result = extract_dataset_from_source(make_source(partner_dir), module.PARTNER_ADAPTER)
    events = [event_from_article(article) for article in result.articles]
    assert [e.title for e in events] == ["Atelier jeux", "Atelier jeux"]      # ludo.csv, lu après le fichier cassé
    assert result.rejection_reasons.get("fichier_illisible") == 1
