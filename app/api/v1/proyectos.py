from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Proyectos"])


@router.get("")
def list_proyectos():
    raise HTTPException(
        status_code=501,
        detail="El módulo de proyectos de ley está en implementación.",
    )
