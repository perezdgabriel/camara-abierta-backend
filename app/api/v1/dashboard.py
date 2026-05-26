from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.dashboard import (
    DashboardResponse,
    DashboardStats,
    PartyComposition,
    RecentEvent,
    TopicCount,
)
from app.schemas.proyectos import BillSummary, PartyBrief
from app.services import dashboard as svc
from app.services import proyectos as bills_svc

_IND_PARTY = PartyBrief(
    id=0, name="Independientes", abbreviation="IND", color="#6b7280"
)

router = APIRouter(tags=["Dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(db: Session = Depends(get_db)):
    data = svc.get_dashboard(db)
    return DashboardResponse(
        stats=DashboardStats.model_validate(data["stats"]),
        recent_events=[RecentEvent.model_validate(e) for e in data["recent_events"]],
        topic_distribution=[
            TopicCount(topic=topic, count=count)
            for topic, count in data["topic_distribution"]
        ],
        chamber_composition=[
            PartyComposition(
                party=_IND_PARTY if party is None else PartyBrief.model_validate(party),
                count=count,
            )
            for party, count in data["chamber_composition"]
        ],
        featured_bills=[
            BillSummary.model_validate(
                {**bill.__dict__, **bills_svc.bill_to_summary_extra(bill)}
            )
            for bill in data["featured_bills"]
        ],
    )
