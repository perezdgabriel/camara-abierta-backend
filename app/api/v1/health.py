from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.health import get_scrape_health

router = APIRouter(tags=["Health"])


@router.get("/scrapes")
def scrape_health(db: Session = Depends(get_db)):
    return get_scrape_health(db)
