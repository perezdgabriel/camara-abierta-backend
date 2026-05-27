from fastapi import APIRouter

from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.legislators import router as legislators_router
from app.api.v1.proyectos import router as proyectos_router
from app.api.v1.reference import router as reference_router
from app.api.v1.voting import router as voting_router

router = APIRouter()
router.include_router(proyectos_router, prefix="/bills")
router.include_router(legislators_router, prefix="/legislators")
router.include_router(voting_router, prefix="/voting-sessions")
router.include_router(dashboard_router, prefix="/dashboard")
router.include_router(reference_router, prefix="/reference")
