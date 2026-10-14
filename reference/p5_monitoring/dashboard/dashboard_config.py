"""
Configuration du dashboard Streamlit.

Ce module centralise les variables d'environnement utilisées
par le dashboard de scoring crédit et de monitoring.

Architecture actuelle
---------------------
- les prédictions utilisent des données issues exclusivement de `application_test.csv`
  côté API
- le dashboard ne lit plus de table SQL de features
- PostgreSQL sert uniquement à l'historique, au logging et au monitoring
"""

from __future__ import annotations

import os

from dotenv import load_dotenv


# =============================================================================
# Chargement des variables d'environnement
# =============================================================================

load_dotenv()


# =============================================================================
# Configuration générale du dashboard
# =============================================================================

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
API_KEY = os.getenv("API_KEY", "")
DEFAULT_LIMIT = int(os.getenv("DEFAULT_LIMIT", "200"))

MODEL_NAME = os.getenv("MODEL_NAME", "credit_scoring_model")
MODEL_VERSION = os.getenv("MODEL_VERSION", "")