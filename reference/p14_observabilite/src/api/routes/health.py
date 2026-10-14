"""Route de vérification de disponibilité de l'API."""

from fastapi import APIRouter

from src.api.schemas import HealthResponse
from src.api.services.provider import inference_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Retourne l'état de disponibilité de l'API."""

    ready = inference_service.is_ready

    return HealthResponse(
        status="ok" if ready else "degraded",
        model_ready=ready,
    )
