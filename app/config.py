"""
Module de configuration de l'application.

**Provenance** : repris de l'Assistant RAG OpenAgenda (projet 6), fichier `app/config.py` (commit 18f7ce1).
Modifié pour SortirIA : ajout des modèles Mistral d'extraction et de modération.

Ce module centralise le chargement :
- des variables d'environnement
- des paramètres globaux du projet
- des chemins utiles à l'application

Les variables sont récupérées depuis un fichier `.env` via la
bibliothèque python-dotenv.

Variables utilisées
-------------------
OPENAGENDA_API_KEY
    Clé API permettant d'interroger l'API OpenAgenda.

ZONE_CHOISIE
    Zone géographique par défaut utilisée pour récupérer les événements.

TYPE_ZONE
    Niveau géographique utilisé pour filtrer les événements
    (ex : city, region, etc.).

DATABASE_URL
    URL de connexion éventuelle à la base de données.

MLFLOW_TRACKING_URI
    URL du serveur MLflow.

API_URL
    URL de l'API locale ou distante.

API_KEY
    Clé personnelle de sécurisation de l'API.

HF_TOKEN
    Token Hugging Face éventuellement utilisé pour certains téléchargements.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Chargement des variables définies dans le fichier .env
load_dotenv()

# ==========================================================
# RACINE DU PROJET ET DOSSIERS UTILES
# ==========================================================

# Racine du projet :
# app/config.py -> app/ -> projet/
BASE_DIR = Path(__file__).resolve().parent.parent

# Dossier de stockage des données du projet
DATA_DIR = BASE_DIR / "data"

# Dossier de l'index FAISS
FAISS_INDEX_DIR = DATA_DIR / "faiss_index_openagenda"

# ==========================================================
# VARIABLES D'ENVIRONNEMENT
# ==========================================================

# Clé API OpenAgenda utilisée pour interroger l'API officielle
OPENAGENDA_API_KEY = os.getenv("OPENAGENDA_API_KEY", "")

# URL de la base de données
DATABASE_URL = os.getenv("DATABASE_URL", "")

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "mistral-embed")

LLM_MODEL = os.getenv("LLM_MODEL", "mistral-small-latest")

# Modèles Mistral utilisés par SortirIA (ajout SortirIA). Les alias « -latest »
# suivent les nouvelles versions sans changer le code ; une version datée peut
# être fixée dans .env pour une démo reproductible.
#   - extraction des contraintes et lecture des pages d'agenda : petit modèle ;
#   - modération / garde-fou des questions et des contenus collectés.
EXTRACTION_MODEL = os.getenv("EXTRACTION_MODEL", "ministral-8b-latest")
MODERATION_MODEL = os.getenv("MODERATION_MODEL", "mistral-moderation-latest")

# URL MLflow
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")

# URL API pour les appels locaux
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

# Zone géographique par défaut utilisée pour les recherches
DEFAULT_ZONE = os.getenv("ZONE_CHOISIE", "Montpellier")

# Portée géographique du filtre (ex : city, region, country)
DEFAULT_SCOPE = os.getenv("TYPE_ZONE", "city")

# Clé personnelle de sécurité API
API_KEY = os.getenv("API_KEY", "")

# Token Hugging Face
HF_TOKEN = os.getenv("HF_TOKEN", "")

if HF_TOKEN:
    os.environ["HF_TOKEN"] = HF_TOKEN