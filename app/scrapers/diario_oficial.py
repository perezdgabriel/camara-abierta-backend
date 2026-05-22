from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.core.session import task_session
from app.scrapers.common import ScraperEngine, jitter_sleep
from app.services.diario_oficial import get_norma_by_cve

BASE_URL = "https://www.diariooficial.interior.gob.cl/edicionelectronica"
HOME_URL = f"{BASE_URL}/"
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "output"


def format_date(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def to_iso_date(date_str: str) -> str:
    dd, mm, yyyy = date_str.split("-")
    return f"{yyyy}-{mm}-{dd}"


def parse_ddmmyyyy(value: str) -> date:
    return datetime.strptime(value, "%d-%m-%Y").date()


def hash_content(title: str, pdf_url: str) -> str:
    return hashlib.sha256(f"{title}|{pdf_url}".encode()).hexdigest()


SCRAPE_JS = r"""
() => {
    const editionEl = document.querySelector('.containerdate .alignleft');
    const dateEl = document.querySelector('.containerdate .date strong');
    const edition = editionEl ? editionEl.textContent.trim() : '';
    const dateText = dateEl ? dateEl.textContent.trim() : '';

    const section = document.querySelector('section.norma_general');
    if (!section) return { edition, date: dateText, norms: [], empty: true };
    if (section.querySelector('p.nofound')) {
        return { edition, date: dateText, norms: [], empty: true };
    }

    const rows = section.querySelectorAll('table tr');
    let currentBranch = '';
    let currentMinistry = '';
    let currentOrgan = '';
    const norms = [];

    rows.forEach(tr => {
        const t3 = tr.querySelector('td.title3');
        if (t3) { currentBranch = t3.textContent.trim(); currentMinistry = ''; currentOrgan = ''; return; }

        const t4 = tr.querySelector('td.title4');
        if (t4) { currentMinistry = t4.textContent.trim(); currentOrgan = ''; return; }

        const t5 = tr.querySelector('td.title5');
        if (t5) { currentOrgan = t5.textContent.trim(); return; }

        if (tr.classList.contains('content')) {
            const tds = tr.querySelectorAll('td');
            const title = tds[0] ? tds[0].textContent.trim().replace(/\s+/g, ' ') : '';
            const link = tds[1] ? tds[1].querySelector('a') : null;
            const pdfUrl = link ? link.href : '';
            const cveM = link ? link.textContent.match(/CVE-(\d+)/) : null;
            const cve = cveM ? cveM[1] : '';
            norms.push({ branch: currentBranch, ministry: currentMinistry, organ: currentOrgan, title, pdfUrl, cve });
        }
    });

    return { edition, date: dateText, norms, empty: false };
}
"""


async def scrape_date(eng: ScraperEngine, date_str: str) -> dict[str, Any] | None:
    url = f"{BASE_URL}/index.php?date={date_str}"
    loaded = await eng.goto_with_retry(
        url, wait_for="section.norma_general, p.nofound, nav.menu"
    )
    if not loaded:
        return None
    data = await eng.page.evaluate(SCRAPE_JS)
    await jitter_sleep(1.0, 3.0)
    return data


def process_local_date(date_str: str) -> dict[str, Any] | None:
    file_path = OUTPUT_DIR / f"{date_str}.json"
    if not file_path.exists():
        return None
    return json.loads(file_path.read_text(encoding="utf-8"))


async def _run_scrape(
    target_date: date, engine: str, headed: bool
) -> dict[str, Any] | None:
    async with ScraperEngine(engine=engine, headed=headed) as eng:
        await eng.warm_up(HOME_URL)
        return await scrape_date(eng, format_date(target_date))


def _build_dispatch_result(
    target_date: date, found: int, dispatched: int, dry_run: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "date": target_date.isoformat(),
        "dry_run": dry_run,
        "found": found,
    }
    if dry_run:
        result["dispatched"] = 0
        result["would_dispatch"] = dispatched
    else:
        result["dispatched"] = dispatched
    return result


def run_scrape(
    target_date: date,
    *,
    engine: str = "playwright",
    headed: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    data = asyncio.run(_run_scrape(target_date, engine, headed))
    if not data or data.get("empty"):
        return _build_dispatch_result(
            target_date, found=0, dispatched=0, dry_run=dry_run
        )

    from app.tasks.normas import process_norma

    dispatched = 0
    date_str = format_date(target_date)
    with task_session() as db:
        for norm in data.get("norms", []):
            cve = norm.get("cve")
            if not cve or get_norma_by_cve(db, cve) is not None:
                continue
            if not dry_run:
                process_norma.delay(
                    cve=cve,
                    pdf_url=norm.get("pdfUrl"),
                    title=norm.get("title") or "",
                    date_value=to_iso_date(date_str),
                    edition=data.get("edition"),
                    branch=norm.get("branch"),
                    ministry=norm.get("ministry"),
                    organ=norm.get("organ"),
                )
            dispatched += 1
    return _build_dispatch_result(
        target_date,
        found=len(data.get("norms", [])),
        dispatched=dispatched,
        dry_run=dry_run,
    )
