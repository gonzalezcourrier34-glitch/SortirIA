"""Route exposant les versions de l'application."""

from fastapi import APIRouter

from src.api.schemas import VersionResponse
from src.api.services.provider import inference_service
from src.api.versions import API_VERSION, PROMPT_VERSION

router = APIRouter(tags=["version"])


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """Retourne les versions du POC."""

    return VersionResponse(
        api_version=API_VERSION,
        model_version=inference_service.model_version,
        prompt_version=PROMPT_VERSION,
    )
