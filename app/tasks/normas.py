from app.core.celery_app import app
from app.core.session import task_session
from app.services.diario_oficial import get_norma_by_cve
from app.services.llm import analyze_norm_text, analyze_norm_with_pdf
from app.services.notifications import send_alerta_norma
from app.services.pdf import download_pdf_bytes, extract_text_from_bytes
from app.services.write import upsert_norma
from app.tasks.base import DatabaseTask


@app.task(
	name="app.tasks.normas.process_norma",
	bind=True,
	base=DatabaseTask,
	queue="llm",
	max_retries=5,
	retry_backoff=True,
	retry_backoff_max=1800,
	soft_time_limit=600,
	time_limit=900,
)
def process_norma(
	self,
	*,
	cve: str,
	pdf_url: str | None,
	title: str,
	date_value: str,
	edition: str | None = None,
	branch: str | None = None,
	ministry: str | None = None,
	organ: str | None = None,
) -> dict:
	with task_session() as db:
		if get_norma_by_cve(db, cve) is not None:
			return {"cve": cve, "status": "already_exists"}

	pdf_bytes = download_pdf_bytes(pdf_url) if pdf_url else None
	highlight = None
	if pdf_bytes:
		try:
			highlight = analyze_norm_with_pdf(pdf_bytes, f"{cve}.pdf", title)
		except Exception:
			extracted_text = extract_text_from_bytes(pdf_bytes)
			if extracted_text:
				highlight = analyze_norm_text(extracted_text, title)
			else:
				raise
	else:
		highlight = analyze_norm_text(title, title)

	with task_session() as db:
		norma = upsert_norma(
			db,
			cve=cve,
			date_value=date_value,
			title=title,
			pdf_url=pdf_url,
			edition=edition,
			branch=branch,
			ministry=ministry,
			organ=organ,
			highlight=highlight,
		)
		norma_id = norma.id

	if highlight:
		send_alerta_norma(highlight, title, cve)
	return {"cve": cve, "norma_id": norma_id, "status": "ok"}