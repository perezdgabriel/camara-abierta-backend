from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.core.session import task_session
from app.models.diario_oficial import Reglamento
from app.scrapers.common import ScraperEngine, jitter_sleep
from app.services.write import compute_reglamento_fingerprint

CGR_BASE = "https://www.contraloria.cl/web/cgr"
FECHA_CAMBIO_GOBIERNO = "2026-03-11"
PERIODO_ANTERIOR_ANIOS = [2022, 2023, 2024, 2025, 2026]
PAGES: list[dict[str, str]] = [
    {"url": f"{CGR_BASE}/tramitacion-de-reglamentos", "categoria": "en_tramite"},
    {"url": f"{CGR_BASE}/tramitacion-de-reglamentos-2026", "categoria": "tramitados"},
    *[
        {
            "url": (
                f"{CGR_BASE}/tramitacion-de-reglamentos-retirados"
                if year == 2022
                else f"{CGR_BASE}/tramitacion-de-reglamentos-retirados-{year}"
            ),
            "categoria": "retirados",
        }
        for year in PERIODO_ANTERIOR_ANIOS
    ],
]

SCRAPE_JS = r"""
() => {
    const dataTable = document.querySelector('table.dataTable');
    if (!dataTable) return [];
    const rows = dataTable.querySelectorAll('tr');
    const results = [];

    for (const tr of rows) {
        const regTd = tr.querySelector('td.reglamento');
        if (!regTd) continue;

        const numero = regTd.textContent.trim();
        const anio = tr.querySelector('td.año')?.textContent.trim() || '';
        const ministerio = tr.querySelector('td.origen')?.textContent.trim() || '';
        const subsecretaria = tr.querySelector('td.subsecretaria')?.textContent.trim() || '';
        const materia = tr.querySelector('td.materia')?.textContent.trim().replace(/\s+/g, ' ') || '';

        const tds = tr.querySelectorAll('td');
        let fechaIngreso = '';
        let estado = '';
        for (let i = 0; i < tds.length; i++) {
            const text = tds[i].textContent.trim();
            if (text.match(/^\d{2}\/\d{2}\/\d{4}$/)) {
                fechaIngreso = text;
                if (i + 1 < tds.length) {
                    const clone = tds[i + 1].cloneNode(true);
                    const itemDiv = clone.querySelector('div.item');
                    if (itemDiv) itemDiv.remove();
                    estado = clone.textContent.trim().replace(/\s+/g, ' ');
                }
                break;
            }
        }

        const etapas = [];
        const detailTable = tr.querySelector('table.detalle');
        if (detailTable) {
            for (const dtr of detailTable.querySelectorAll('tr')) {
                const dtds = dtr.querySelectorAll('td');
                if (dtds.length < 2) continue;

                const docTd = dtds[5];
                let documento = '';
                let documentoUrl = '';
                if (docTd) {
                    const link = docTd.querySelector('a');
                    if (link) {
                        documento = link.textContent.trim();
                        documentoUrl = link.href || '';
                    } else {
                        documento = docTd.textContent.trim();
                    }
                }

                etapas.push({
                    etapa: dtds[0]?.textContent.trim() || '',
                    fecha: dtds[1]?.textContent.trim() || '',
                    accion: dtds[2]?.textContent.trim().replace(/\s+/g, ' ') || '',
                    sector: dtds[3]?.textContent.trim().replace(/\s+/g, ' ') || '',
                    observaciones: dtds[4]?.textContent.trim().replace(/\s+/g, ' ') || '',
                    documento,
                    documento_url: documentoUrl,
                });
            }
        }

        results.push({ numero, anio, ministerio, subsecretaria, materia, fecha_ingreso: fechaIngreso, estado, etapas, reingresado: false });
    }

    return results;
}
"""


def _parse_date(date_str: str) -> str | None:
    if not date_str:
        return None
    parts = date_str.split("/")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    dd, mm, yyyy = parts
    return f"{yyyy}-{mm}-{dd}"


def _mark_reingresado(reglamentos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mark reglamentos that were retirado during the current government and re-entered.

    Only called for en_tramite/tramitados categories. A reglamento here with a retiro
    etapa dated >= FECHA_CAMBIO_GOBIERNO was necessarily retirado then re-ingresado
    (otherwise it would be in the retirados category, not here)."""
    for reglamento in reglamentos:
        reglamento["reingresado"] = any(
            "retiro" in (etapa.get("accion") or "").lower()
            and (fecha := _parse_date(etapa.get("fecha", ""))) is not None
            and fecha >= FECHA_CAMBIO_GOBIERNO
            for etapa in reglamento.get("etapas", [])
        )
    return reglamentos


async def scrape_page(eng: ScraperEngine, url: str) -> list[dict[str, Any]]:
    loaded = await eng.goto_with_retry(url, wait_for="table.dataTable")
    if not loaded:
        return []
    results = await eng.page.evaluate(SCRAPE_JS)
    await jitter_sleep(1.0, 2.5)
    return results


async def _collect(engine: str, headed: bool) -> dict[str, list[dict[str, Any]]]:
    all_results: dict[str, list[dict[str, Any]]] = {}
    async with ScraperEngine(engine=engine, headed=headed) as eng:
        await eng.warm_up(f"{CGR_BASE}/tramitacion-de-reglamentos")
        for page_cfg in PAGES:
            regs = await scrape_page(eng, page_cfg["url"])
            all_results.setdefault(page_cfg["categoria"], []).extend(regs)
    return all_results


def _build_dispatch_result(total: int, dispatched: int, dry_run: bool) -> dict[str, int | bool]:
    result: dict[str, int | bool] = {"dry_run": dry_run, "total": total}
    if dry_run:
        result["dispatched"] = 0
        result["would_dispatch"] = dispatched
    else:
        result["dispatched"] = dispatched
    return result


def run_scrape(*, engine: str = "playwright", headed: bool = False, dry_run: bool = False) -> dict[str, int | bool]:
    all_results = asyncio.run(_collect(engine, headed))

    if "retirados" in all_results:
        filtered = []
        for reglamento in all_results["retirados"]:
            retiro_fecha = None
            for etapa in reglamento.get("etapas", []):
                fecha = _parse_date(etapa.get("fecha", ""))
                if "retiro" in (etapa.get("accion") or "").lower() and fecha and fecha >= FECHA_CAMBIO_GOBIERNO:
                    retiro_fecha = fecha
            if retiro_fecha:
                filtered.append({**reglamento, "fecha_retiro": retiro_fecha})
        all_results["retirados"] = filtered

    for category in ("en_tramite", "tramitados"):
        if category in all_results:
            all_results[category] = _mark_reingresado(all_results[category])

    from app.tasks.reglamentos import sync_reglamento

    dispatched = 0
    total = 0
    with task_session() as db:
        for category, reglamentos in all_results.items():
            total += len(reglamentos)
            for reglamento in reglamentos:
                payload = {**reglamento, "categoria": category}
                fingerprint = compute_reglamento_fingerprint(payload)
                existing = db.execute(
                    select(Reglamento).where(Reglamento.numero == payload["numero"])
                    .where(Reglamento.anio == payload["anio"])
                    .where(Reglamento.ministerio == payload["ministerio"])
                    .where(Reglamento.categoria == category)
                ).scalar_one_or_none()
                if existing is not None and existing.content_fingerprint == fingerprint:
                    continue
                if not dry_run:
                    sync_reglamento.delay({**payload, "content_fingerprint": fingerprint})
                dispatched += 1
    return _build_dispatch_result(total=total, dispatched=dispatched, dry_run=dry_run)