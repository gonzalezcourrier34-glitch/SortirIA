"""Configuration commune des tests CheckIt.AI.

**Provenance** : repris de CheckIt.AI (projet 12, moteur de collecte), fichier `tests/conftest.py` (commit 93632c5).
Modifié pour SortirIA : création de secrets factices pour que les tests tournent sans clé d'API (poste neuf, CI).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Racine du projet

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Secrets factices pour les tests (ajout SortirIA)
#
# Les tests des extracteurs repris de CheckIt.AI lisent la configuration des
# sources, qui exige que chaque clé d'API existe. Sur un poste neuf ou en CI,
# le dossier secrets/ n'existe pas : sans ce bloc, ces tests échouent avant
# même de tester leur logique. On crée donc des secrets factices dans un
# dossier temporaire, uniquement si aucun dossier de secrets n'est configuré.
# Les vrais secrets ne sont jamais lus ni modifiés.

import os
import re
import tempfile

if "CHECKIT_LOCAL_SECRETS_DIR" not in os.environ and not (PROJECT_ROOT / "secrets").is_dir():
    _sources_text = (PROJECT_ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")
    _secret_names = set(re.findall(r'_secret_name:\s*"([a-z0-9_]+)"', _sources_text))
    _fake_secrets_dir = Path(tempfile.mkdtemp(prefix="sortiria_test_secrets_"))
    for _name in _secret_names:
        (_fake_secrets_dir / _name).write_text("secret-factice-pour-les-tests", encoding="utf-8")
    os.environ["CHECKIT_LOCAL_SECRETS_DIR"] = str(_fake_secrets_dir)
