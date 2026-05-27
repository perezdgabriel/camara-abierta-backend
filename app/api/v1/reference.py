from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.core import Region
from app.models.legislature import PoliticalParty
from app.schemas.legislators import PartyBrief
from app.schemas.reference import RegionBrief

router = APIRouter(tags=["Reference"])


@router.get("/parties", response_model=list[PartyBrief])
def list_parties(db: Session = Depends(get_db)):
    rows = (
        db.query(PoliticalParty)
        .filter(PoliticalParty.is_active.is_(True))
        .order_by(PoliticalParty.name.asc())
        .all()
    )
    return [PartyBrief.model_validate(row) for row in rows]


@router.get("/regions", response_model=list[RegionBrief])
def list_regions(db: Session = Depends(get_db)):
    rows = db.query(Region).order_by(Region.number.asc()).all()
    return [RegionBrief.model_validate(row) for row in rows]
