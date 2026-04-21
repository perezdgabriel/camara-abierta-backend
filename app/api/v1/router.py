from fastapi import APIRouter

from app.api.v1.diario_oficial import router as diario_oficial_router
from app.api.v1.proyectos import router as proyectos_router
from app.api.v1.reglamentos import router as reglamentos_router
from app.api.v1.sync import router as sync_router

router = APIRouter()
router.include_router(diario_oficial_router, prefix="/diario-oficial")
router.include_router(reglamentos_router, prefix="/reglamentos")
router.include_router(proyectos_router, prefix="/proyectos")
router.include_router(sync_router, prefix="/sync")
