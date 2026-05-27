from datetime import date
from types import SimpleNamespace

from app.services.health import get_scrape_health


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class FakeDB:
    def __init__(self, states, latest_norma_date):
        self.results = [FakeScalarResult(states), FakeScalarResult(latest_norma_date)]

    def execute(self, stmt):
        return self.results.pop(0)


def test_get_scrape_health_includes_ingestor_cursors():
    db = FakeDB(
        states=[
            SimpleNamespace(
                entity_type="reference",
                last_sync_date=date(2026, 5, 27),
                last_cursor=None,
            ),
            SimpleNamespace(
                entity_type="geography",
                last_sync_date=date(2026, 5, 28),
                last_cursor="2026-05-27",
            ),
        ],
        latest_norma_date=date(2026, 5, 26),
    )

    result = get_scrape_health(db)

    assert result == {
        "ingestors": {
            "reference": "2026-05-27",
            "geography": "2026-05-28",
        },
        "ingestor_cursors": {
            "reference": None,
            "geography": "2026-05-27",
        },
        "latest_norma_date": "2026-05-26",
    }
