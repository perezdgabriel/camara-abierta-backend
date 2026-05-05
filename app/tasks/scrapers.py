from datetime import date

from app.core.celery_app import app
from app.scrapers.cgr_reglamentos import run_scrape as run_reglamentos_scrape
from app.scrapers.diario_oficial import run_scrape as run_diario_scrape
from app.tasks.base import DatabaseTask


@app.task(
	name="app.tasks.scrapers.scrape_diario_oficial",
	bind=True,
	base=DatabaseTask,
	soft_time_limit=600,
	time_limit=600,
)
def scrape_diario_oficial(self, target_date: str | None = None) -> dict:
	parsed_date = date.fromisoformat(target_date) if target_date else date.today()
	return run_diario_scrape(parsed_date)


@app.task(
	name="app.tasks.scrapers.scrape_cgr_reglamentos",
	bind=True,
	base=DatabaseTask,
	soft_time_limit=600,
	time_limit=600,
)
def scrape_cgr_reglamentos(self) -> dict:
	return run_reglamentos_scrape()