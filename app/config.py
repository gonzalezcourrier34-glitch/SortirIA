"""Configuration de l'API SortirIA.

**Provenance** : ce fichier venait de l'Assistant RAG OpenAgenda (projet 6). Le
15/10/2026, l'ancien RAG (index FAISS, tableau Streamlit) a été retiré : il ne
reste que la clé API qui protège `/events/{event_id}/notes` (`app/security.py`).
Les autres réglages (base, modèles Mistral, MLflow) sont lus là où ils servent,
depuis le `.env` chargé par `config/environment.py`.
"""

from __future__ import annotations

import os

from config.environment import load_environment

load_environment()

API_KEY = os.getenv("API_KEY", "")
