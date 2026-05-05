import logging
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient

logger = logging.getLogger(__name__)

NS = "http://tempuri.org/"
NS_BRACE = f"{{{NS}}}"


class CamaraClient(BaseCongresoClient):
    BASE_URL = settings.ingestor_base_url_camara

    def get_diputados_vigentes(self) -> list[dict]:
        root = self._get_xml("getDiputados_Vigentes")
        deputies = [self._parse_diputado(dip) for dip in root.iter(f"{NS_BRACE}Diputado")]
        if not deputies:
            deputies = [self._parse_diputado(dip, ns="") for dip in root.iter("Diputado")]
        logger.info("Fetched %d deputies", len(deputies))
        return deputies

    def _parse_diputado(self, dip: ET.Element, ns: str = NS_BRACE) -> dict:
        sexo_el = dip.find(f"{ns}Sexo")
        gender_code = sexo_el.get("Codigo", "") if sexo_el is not None else ""

        militancia = dip.find(f"{ns}Militancia_Actual")
        party = ""
        if militancia is not None:
            partido_el = militancia.find(f"{ns}Partido")
            if partido_el is not None:
                party = (partido_el.text or partido_el.get("Codigo", "") or "").strip()

        ejercicio = dip.find(f"{ns}Ejercicio_Periodo_Legislativo_Actual")
        district = ""
        period_id = ""
        is_active = False
        if ejercicio is not None:
            d_el = ejercicio.find(f"{ns}Distrito")
            if d_el is not None and d_el.text:
                district = d_el.text.strip()
            p_el = ejercicio.find(f"{ns}Periodo")
            if p_el is not None:
                id_el = p_el.find(f"{ns}ID")
                if id_el is not None and id_el.text:
                    period_id = id_el.text.strip()
            e_el = ejercicio.find(f"{ns}Estado")
            if e_el is not None and e_el.text:
                is_active = e_el.text.strip().lower() == "true"

        return {
            "dipid": self._ns_text(dip, "DIPID", ns),
            "first_name": self._ns_text(dip, "Nombre", ns),
            "last_name_father": self._ns_text(dip, "Apellido_Paterno", ns),
            "last_name_mother": self._ns_text(dip, "Apellido_Materno", ns),
            "birth_date": self._parse_date_iso(self._ns_text(dip, "Fecha_Nacimiento", ns)),
            "gender_code": gender_code,
            "party": party,
            "district": district,
            "period_id": period_id,
            "is_active": is_active,
            "email": self._ns_text(dip, "Correo_Electronico", ns),
        }

    def get_comisiones_vigentes(self) -> list[dict]:
        root = self._get_xml("getComisiones_Vigentes")
        committees = []
        ns = NS_BRACE
        for com in root.iter(f"{ns}Comision"):
            members = [
                {
                    "dipid": self._ns_text(integ, "DIPID", ns),
                    "first_name": self._ns_text(integ, "Nombre", ns),
                    "last_name_father": self._ns_text(integ, "Apellido_Paterno", ns),
                    "last_name_mother": self._ns_text(integ, "Apellido_Materno", ns),
                    "role": self._ns_text(integ, "Cargo", ns),
                }
                for integ in com.iter(f"{ns}Integrante")
            ]
            committees.append(
                {
                    "id": self._ns_text(com, "ID", ns),
                    "name": self._ns_text(com, "Nombre", ns),
                    "type": self._ns_text(com, "Tipo", ns),
                    "members": members,
                }
            )
        logger.info("Fetched %d deputy committees", len(committees))
        return committees

    def get_votaciones_boletin(self, bulletin: str) -> list[dict]:
        boletin_num = bulletin.split("-")[0]
        root = self._get_xml("getVotaciones_Boletin", params={"prmBoletin": boletin_num})
        ns = NS_BRACE
        votes = [
            {
                "id": self._ns_text(vot, "ID", ns),
                "date": self._parse_date_iso(self._ns_text(vot, "Fecha", ns)),
                "subject": self._ns_text(vot, "Descripcion", ns) or self._ns_text(vot, "Materia", ns),
                "result": self._ns_text(vot, "Resultado", ns),
                "total_for": self._ns_int(vot, "TotalAfirmativos", ns),
                "total_against": self._ns_int(vot, "TotalNegativos", ns),
                "total_abstentions": self._ns_int(vot, "TotalAbstenciones", ns),
            }
            for vot in root.iter(f"{NS_BRACE}Votacion")
        ]
        logger.info("Fetched %d votes for bulletin %s", len(votes), bulletin)
        return votes

    def get_legislaturas(self) -> list[dict]:
        root = self._get_xml("getLegislaturas")
        ns = NS_BRACE
        return [
            {
                "id": self._ns_text(leg, "ID", ns),
                "number": self._ns_int(leg, "Numero", ns),
                "type": self._ns_text(leg, "Tipo", ns),
                "start_date": self._parse_date_iso(self._ns_text(leg, "FechaInicio", ns)),
                "end_date": self._parse_date_iso(self._ns_text(leg, "FechaTermino", ns)),
            }
            for leg in root.iter(f"{NS_BRACE}Legislatura")
        ]

    def get_periodos_legislativos(self) -> list[dict]:
        root = self._get_xml("getPeriodosLegislativos")
        ns = NS_BRACE
        return [
            {
                "id": self._ns_text(per, "ID", ns),
                "name": self._ns_text(per, "Nombre", ns),
                "start_date": self._parse_date_iso(self._ns_text(per, "FechaInicio", ns)),
                "end_date": self._parse_date_iso(self._ns_text(per, "FechaTermino", ns)),
            }
            for per in root.iter(f"{NS_BRACE}PeriodoLegislativo")
        ]

    def get_sesiones(self, legislatura_id: str) -> list[dict]:
        root = self._get_xml("getSesiones", params={"prmLegislaturaID": legislatura_id})
        ns = NS_BRACE
        return [
            {
                "id": self._ns_text(ses, "ID", ns),
                "number": self._ns_int(ses, "Numero", ns),
                "type": self._ns_text(ses, "Tipo", ns),
                "date": self._parse_date_iso(self._ns_text(ses, "FechaInicio", ns)),
                "end_date": self._parse_date_iso(self._ns_text(ses, "FechaTermino", ns)),
            }
            for ses in root.iter(f"{NS_BRACE}Sesion")
        ]

    @staticmethod
    def _ns_text(element: ET.Element, tag: str, ns: str) -> str:
        child = element.find(f"{ns}{tag}")
        if child is not None and child.text:
            return child.text.strip()
        return ""

    @staticmethod
    def _ns_int(element: ET.Element, tag: str, ns: str) -> int:
        child = element.find(f"{ns}{tag}")
        if child is not None and child.text:
            try:
                return int(child.text.strip())
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _parse_date_iso(value: str) -> str | None:
        if not value:
            return None
        import re

        match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
        if match:
            return match.group(1)
        match = re.match(r"(\d{2})/(\d{2})/(\d{4})", value)
        if match:
            return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
        return None