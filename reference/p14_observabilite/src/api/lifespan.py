"""Gestion du cycle de vie de l'API."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.services.provider import inference_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise le moteur d'inférence au démarrage."""
    inference_service.load()
    yield
