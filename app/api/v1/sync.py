from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["Sync"])


@router.get("")
def sync_status():
    raise HTTPException(
        status_code=501,
        detail="El módulo de delta sync está en implementación.",
    )
