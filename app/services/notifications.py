import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


def _send_email(subject: str, html: str) -> None:
    if not settings.resend_api_key or not settings.notification_email:
        logger.warning(
            "Resend or notification recipient is not configured; skipping email"
        )
        return

    import resend

    resend.api_key = settings.resend_api_key
    resend.Emails.send(
        {
            "from": settings.notification_from_email,
            "to": [settings.notification_email],
            "subject": subject,
            "html": html,
        }
    )


def send_alerta_reglamento(reglamento: dict[str, Any], change_type: str) -> None:
    subject = f"[Reglamentos CGR] {change_type}: {reglamento.get('numero')} ({reglamento.get('anio')})"
    html = f"""
    <h2>Actualizacion de reglamento CGR</h2>
    <p><strong>Tipo:</strong> {change_type}</p>
    <p><strong>Numero:</strong> {reglamento.get("numero")}</p>
    <p><strong>Anio:</strong> {reglamento.get("anio")}</p>
    <p><strong>Ministerio:</strong> {reglamento.get("ministerio")}</p>
    <p><strong>Categoria:</strong> {reglamento.get("categoria")}</p>
    <p><strong>Estado:</strong> {reglamento.get("estado") or ""}</p>
    <p><strong>Materia:</strong> {reglamento.get("materia") or ""}</p>
    """
    _send_email(subject, html)


def send_alerta_proyecto(
    bulletin_number: str,
    title: str,
    change_type: str,
    extra: dict[str, Any] | None = None,
) -> None:
    extra_info = extra or {}
    labels = {
        "new": "Nuevo proyecto de ley",
        "status_changed": "Cambio de estado",
        "stage_changed": "Cambio de etapa",
    }
    label = labels.get(change_type, change_type)

    subject = f"[Proyectos de Ley] {label}: {bulletin_number}"
    html = f"""
    <h2>{label}</h2>
    <p><strong>Boletin:</strong> {bulletin_number}</p>
    <p><strong>Titulo:</strong> {title[:200]}</p>
    """
    if extra_info.get("old_status"):
        html += f"<p><strong>Estado anterior:</strong> {extra_info['old_status']}</p>"
    if extra_info.get("new_status"):
        html += f"<p><strong>Estado nuevo:</strong> {extra_info['new_status']}</p>"
    if extra_info.get("entry_date"):
        html += f"<p><strong>Fecha ingreso:</strong> {extra_info['entry_date']}</p>"
    if extra_info.get("origin"):
        html += f"<p><strong>Origen:</strong> {extra_info['origin']}</p>"
    _send_email(subject, html)


def send_alerta_norma(highlight_json: dict[str, Any], titulo: str, cve: str) -> None:
    score = int(highlight_json.get("importancia_ciudadana", 0) or 0)
    if score <= 8:
        return

    puntos = "".join(
        f"<li>{punto}</li>" for punto in highlight_json.get("puntos_clave", [])
    )
    subject = f"[Alta Importancia] {highlight_json.get('titulo_amigable', titulo)[:80]}"
    html = f"""
    <h2>Norma de alta importancia ciudadana</h2>
    <p><strong>CVE:</strong> {cve}</p>
    <p><strong>Puntaje:</strong> {score}/10</p>
    <p><strong>Titulo:</strong> {titulo}</p>
    <p><strong>Titulo amigable:</strong> {highlight_json.get("titulo_amigable", "")}</p>
    <p><strong>Resumen:</strong> {highlight_json.get("resumen_ejecutivo", "")}</p>
    <p><strong>Categoria:</strong> {highlight_json.get("categoria", "")}</p>
    <p><strong>Beneficiarios:</strong> {highlight_json.get("beneficiarios", "")}</p>
    <ul>{puntos}</ul>
    """
    _send_email(subject, html)
