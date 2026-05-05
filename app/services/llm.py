import json
import logging
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Eres un Analista de Politicas Publicas experto en Chile. Tu mision es traducir textos legales a lenguaje ciudadano.

REGLAS:
- Responde exclusivamente en JSON valido.
- Categorias: [Salud, Economia, Trabajo, Vivienda, Educacion, Justicia, Nombramientos, Otros].
- Tono: Cercano y sin tecnicismos.

ESTRUCTURA JSON:
{
  \"titulo_amigable\": \"string\",
  \"resumen_ejecutivo\": \"string (max 280 carac.)\",
  \"puntos_clave\": [\"lista\"],
  \"beneficiarios\": \"string\",
  \"categoria\": \"string\",
  \"importancia_ciudadana\": 1-10
}"""

REQUIRED_KEYS = {
    "titulo_amigable",
    "resumen_ejecutivo",
    "puntos_clave",
    "beneficiarios",
    "categoria",
    "importancia_ciudadana",
}


def _httpx():
    import httpx

    return httpx


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openwebui_api_key}",
        "Accept": "application/json",
    }


def _parse_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(line for line in lines if not line.strip().startswith("```"))
    data = json.loads(cleaned)
    missing = REQUIRED_KEYS - set(data.keys())
    if missing:
        raise ValueError(f"Missing keys in LLM response: {sorted(missing)}")
    data["importancia_ciudadana"] = int(data["importancia_ciudadana"])
    if not isinstance(data["puntos_clave"], list):
        data["puntos_clave"] = [str(data["puntos_clave"])]
    return data


def upload_pdf(pdf_bytes: bytes, filename: str) -> str:
    if not settings.openwebui_api_key:
        raise RuntimeError("OPENWEBUI_API_KEY is not configured")

    httpx = _httpx()
    with httpx.Client(base_url=settings.openwebui_url, timeout=60) as client:
        response = client.post(
            "/api/v1/files/",
            headers=_headers(),
            files={"file": (filename, pdf_bytes, "application/pdf")},
        )
        response.raise_for_status()
        file_id = response.json()["id"]

        started_at = time.time()
        while time.time() - started_at < settings.file_process_timeout:
            status_response = client.get(
                f"/api/v1/files/{file_id}/process/status",
                headers=_headers(),
            )
            status_response.raise_for_status()
            status = status_response.json().get("status")
            if status == "completed":
                return file_id
            if status == "failed":
                error = status_response.json().get("error", "unknown")
                raise RuntimeError(f"Open WebUI file processing failed: {error}")
            time.sleep(2)

    raise TimeoutError(
        f"File processing did not complete within {settings.file_process_timeout}s"
    )


def delete_file(file_id: str) -> None:
    if not settings.openwebui_api_key:
        return
    httpx = _httpx()
    try:
        with httpx.Client(base_url=settings.openwebui_url, timeout=10) as client:
            client.delete(f"/api/v1/files/{file_id}", headers=_headers())
    except Exception as exc:
        logger.warning("Failed to delete Open WebUI file %s: %s", file_id, exc)


def analyze_norm_with_pdf(pdf_bytes: bytes, filename: str, titulo: str) -> dict[str, Any]:
    if settings.openwebui_api_key:
        try:
            return _openwebui_with_pdf(pdf_bytes, filename, titulo)
        except Exception as exc:
            logger.warning("Open WebUI PDF analysis failed, falling back to Gemini: %s", exc)
    return _gemini_with_pdf(pdf_bytes, titulo)


def analyze_norm_text(texto: str, titulo: str) -> dict[str, Any]:
    if settings.openwebui_api_key:
        try:
            return _openwebui_text(texto, titulo)
        except Exception as exc:
            logger.warning("Open WebUI text analysis failed, falling back to Gemini: %s", exc)
    return _gemini_text(texto, titulo)


def _openwebui_with_pdf(pdf_bytes: bytes, filename: str, titulo: str) -> dict[str, Any]:
    if not settings.openwebui_api_key:
        raise RuntimeError("OPENWEBUI_API_KEY is not configured")

    file_id = upload_pdf(pdf_bytes, filename)
    httpx = _httpx()
    try:
        prompt = (
            "Analiza la siguiente norma del Diario Oficial de Chile.\n\n"
            f"Titulo: {titulo}\n\n"
            "El documento PDF adjunto contiene el texto completo de la norma."
        )
        with httpx.Client(base_url=settings.openwebui_url, timeout=120) as client:
            response = client.post(
                "/api/chat/completions",
                headers={**_headers(), "Content-Type": "application/json"},
                json={
                    "model": settings.openwebui_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "files": [{"type": "file", "id": file_id}],
                },
            )
            response.raise_for_status()
            return _parse_json(response.json()["choices"][0]["message"]["content"])
    finally:
        delete_file(file_id)


def _openwebui_text(texto: str, titulo: str) -> dict[str, Any]:
    if not settings.openwebui_api_key:
        raise RuntimeError("OPENWEBUI_API_KEY is not configured")

    prompt = (
        "Analiza la siguiente norma del Diario Oficial de Chile.\n\n"
        f"Titulo: {titulo}\n\n"
        f"Texto:\n{texto[:7000]}"
    )
    httpx = _httpx()
    with httpx.Client(base_url=settings.openwebui_url, timeout=120) as client:
        response = client.post(
            "/api/chat/completions",
            headers={**_headers(), "Content-Type": "application/json"},
            json={
                "model": settings.openwebui_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            },
        )
        response.raise_for_status()
        return _parse_json(response.json()["choices"][0]["message"]["content"])


def _gemini_client():
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    from google import genai

    return genai.Client(api_key=settings.gemini_api_key)


def _gemini_with_pdf(pdf_bytes: bytes, titulo: str) -> dict[str, Any]:
    from google.genai import types

    client = _gemini_client()
    prompt = (
        "Analiza la siguiente norma del Diario Oficial de Chile.\n\n"
        f"Titulo: {titulo}\n\n"
        "El documento PDF adjunto contiene el texto completo de la norma."
    )
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
        ),
    )
    return _parse_json(response.text)


def _gemini_text(texto: str, titulo: str) -> dict[str, Any]:
    from google.genai import types

    client = _gemini_client()
    prompt = (
        "Analiza la siguiente norma del Diario Oficial de Chile.\n\n"
        f"Titulo: {titulo}\n\n"
        f"Texto:\n{texto[:7000]}"
    )
    response = client.models.generate_content(
        model=settings.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
        ),
    )
    return _parse_json(response.text)