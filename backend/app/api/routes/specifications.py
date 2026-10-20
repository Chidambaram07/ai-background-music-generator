from fastapi import APIRouter

from app.core.config import get_settings
from app.core.http import IntegrationRoute
from app.schemas.specification import SpecificationRequest, SpecificationResponse
from app.services.specification_service import SpecificationService

router = APIRouter(prefix="/api/v1/specifications", tags=["specifications"], route_class=IntegrationRoute)


@router.post("", response_model=SpecificationResponse, summary="Convert a natural-language music prompt into a validated music specification")
async def create_specification(payload: SpecificationRequest) -> SpecificationResponse:
    settings = get_settings()
    service = SpecificationService(settings)
    specification = await service.create_specification(
        payload.prompt,
        {
            "genre": payload.genre,
            "mood": payload.mood,
            "tempo": payload.tempo,
            "key": payload.key,
            "instruments": payload.instruments,
            "sections": payload.sections,
        },
    )
    return SpecificationResponse(specification=specification)
