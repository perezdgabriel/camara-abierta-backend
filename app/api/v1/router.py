from fastapi import APIRouter

from app.api.v1.diario_oficial import router as diario_oficial_router
from app.api.v1.health import router as health_router
from app.api.v1.legislators import router as legislators_router
from app.api.v1.proyectos import router as proyectos_router
from app.api.v1.reglamentos import router as reglamentos_router
from app.api.v1.sync import router as sync_router
from app.api.v1.voting import router as voting_router

router = APIRouter()
router.include_router(diario_oficial_router, prefix="/diario-oficial")
router.include_router(reglamentos_router, prefix="/reglamentos")
router.include_router(proyectos_router, prefix="/bills")
router.include_router(legislators_router, prefix="/legislators")
router.include_router(voting_router, prefix="/voting-sessions")
router.include_router(sync_router, prefix="/sync")
router.include_router(health_router, prefix="/health")
