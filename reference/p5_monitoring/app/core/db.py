"""
Configuration de la base de données PostgreSQL avec SQLAlchemy.

Ce module centralise :
- la lecture de l'URL de connexion
- la création du moteur SQLAlchemy
- la création des sessions
- la base déclarative des modèles
- la dépendance FastAPI pour accéder à la base

Notes
-----
- En environnement Docker, une URL par défaut est fournie.
- Chaque requête API utilise sa propre session SQLAlchemy.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


# Chargement des variables d'environnement
load_dotenv()


# Configuration de la base de données
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@postgres:5432/credit_api"
)


# Initialisation du moteur SQLAlchemy
engine = create_engine(
    DATABASE_URL,
    echo=False,
)


# Gestion des sessions
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# Base déclarative
Base = declarative_base()


# Dépendance FastAPI
def get_db():
    """
    Fournit une session SQLAlchemy à une requête FastAPI.

    Yields
    ------
    Session
        Session active connectée à PostgreSQL.

    Notes
    -----
    - Une nouvelle session est créée pour chaque requête.
    - La session est fermée automatiquement en fin d'utilisation.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()