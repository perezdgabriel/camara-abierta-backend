import datetime
import logging
from typing import Any
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient

logger = logging.getLogger(__name__)


class SenadoClient(BaseCongresoClient):
    BASE_URL = settings.ingestor_base_url_senado

    def get_senadores_vigentes(self) -> list[dict[str, Any]]:
        root = self._get_xml("senadores_vigentes.php")
        senators = [
            {
                "parlid": self._text(sen, "PARLID"),
                "first_name": self._text(sen, "PARLNOMBRE"),
                "last_name_father": self._text(sen, "PARLAPELLIDOPATERNO"),
                "last_name_mother": self._text(sen, "PARLAPELLIDOMATERNO"),
                "region": self._text(sen, "REGION"),
                "circumscription": self._text(sen, "CIRCUNSCRIPCION"),
                "party": self._text(sen, "PARTIDO"),
                "phone": self._text(sen, "FONO"),
                "email": self._text(sen, "EMAIL"),
            }
            for sen in root.iter("senador")
        ]
        logger.info("Fetched %d senators", len(senators))
        return senators

    def get_comisiones(self) -> list[dict[str, Any]]:
        root = self._get_xml("comisiones.php")
        committees = [
            {
                "id": self._text(com, "id"),
                "name": self._text(com, "nombre"),
                "type": self._text(com, "tipo"),
                "email": self._text(com, "email"),
                "members": [
                    {
                        "parlid": self._text(integ, "PARLID"),
                        "first_name": self._text(integ, "NOMBRE"),
                        "last_name_father": self._text(integ, "APELLIDO_PATERNO"),
                        "last_name_mother": self._text(integ, "APELLIDO_MATERNO"),
                        "role": self._text(integ, "FUNCION"),
                    }
                    for integrantes in [com.find("integrantes")]
                    if integrantes is not None
                    for integ in integrantes.iter("integrante")
                ],
            }
            for com in root.iter("comision")
        ]
        logger.info("Fetched %d senate committees", len(committees))
        return committees

    def get_bill_by_bulletin(self, bulletin: str) -> dict[str, Any] | None:
        boletin_num = bulletin.split("-")[0]
        root = self._get_xml("tramitacion.php", params={"boletin": boletin_num})
        return SenadoClient._parse_bill_xml(root, bulletin)

    @staticmethod
    def _parse_bill_xml(root: ET.Element, bulletin: str) -> dict[str, Any] | None:
        proyecto = root.find(".//proyecto")
        if proyecto is None:
            logger.warning("No project found for bulletin %s", bulletin)
            return None

        desc = proyecto.find("descripcion")
        bill: dict[str, Any] = {
            "bulletin": BaseCongresoClient._text(desc, "boletin"),
            "title": BaseCongresoClient._text(desc, "titulo"),
            "entry_date": SenadoClient._parse_date_dmy(BaseCongresoClient._text(desc, "fecha_ingreso")),
            "initiative": BaseCongresoClient._text(desc, "iniciativa"),
            "origin_chamber": BaseCongresoClient._text(desc, "camara_origen"),
            "current_urgency": BaseCongresoClient._text(desc, "urgencia_actual"),
            "stage": BaseCongresoClient._text(desc, "etapa"),
            "substage": BaseCongresoClient._text(desc, "subetapa"),
            "law_number": BaseCongresoClient._text(desc, "leynro"),
            "publication_date": SenadoClient._parse_date_dmy(BaseCongresoClient._text(desc, "diariooficial")),
            "status": BaseCongresoClient._text(desc, "estado"),
            "message_url": BaseCongresoClient._text(desc, "link_mensaje_mocion"),
        }
        bill["authors"] = SenadoClient._parse_authors(proyecto)
        bill["tramitaciones"] = SenadoClient._parse_tramitaciones(proyecto)
        bill["votaciones"] = SenadoClient._parse_votaciones(proyecto)
        bill["informes"] = SenadoClient._parse_informes(proyecto)
        bill["comparados"] = SenadoClient._parse_comparados(proyecto)
        bill["oficios"] = SenadoClient._parse_oficios(proyecto)
        bill["materias"] = [BaseCongresoClient._text(m, "DESCRIPCION") for m in proyecto.iter("materia")]
        return bill

    def get_bills_by_date(self, since: datetime.date) -> list[str]:
        fecha = since.strftime("%d/%m/%Y")
        root = self._get_xml("tramitacion.php", params={"fecha": fecha})
        bulletins = [
            boletin
            for proyecto in root.iter("proyecto")
            for desc in [proyecto.find("descripcion")]
            for boletin in [self._text(desc, "boletin")]
            if boletin
        ]
        logger.info("Found %d bills modified since %s", len(bulletins), fecha)
        return bulletins

    def get_votes_by_bulletin(self, bulletin: str) -> list[dict[str, Any]]:
        boletin_num = bulletin.split("-")[0]
        root = self._get_xml("votaciones.php", params={"boletin": boletin_num})
        return SenadoClient._parse_votaciones_from_root(root)

    @staticmethod
    def _parse_authors(proyecto: ET.Element) -> list[dict[str, Any]]:
        return [{"legislator": BaseCongresoClient._text(auth, "PARLAMENTARIO")} for auth in proyecto.iter("autor")]

    @staticmethod
    def _parse_tramitaciones(proyecto: ET.Element) -> list[dict[str, Any]]:
        return [
            {
                "session": BaseCongresoClient._text(tram, "SESION"),
                "date": SenadoClient._parse_date_dmy(BaseCongresoClient._text(tram, "FECHA")),
                "description": BaseCongresoClient._text(tram, "DESCRIPCIONTRAMITE"),
                "stage": BaseCongresoClient._text(tram, "ETAPDESCRIPCION"),
                "chamber": BaseCongresoClient._text(tram, "CAMARATRAMITE"),
            }
            for tram in proyecto.iter("tramite")
        ]

    @staticmethod
    def _parse_votaciones(proyecto: ET.Element) -> list[dict[str, Any]]:
        return [
            {
                "session": BaseCongresoClient._text(vot, "SESION"),
                "date": SenadoClient._parse_date_dmy(BaseCongresoClient._text(vot, "FECHA")),
                "subject": BaseCongresoClient._text(vot, "TEMA"),
                "votes_for": SenadoClient._int(vot, "SI"),
                "votes_against": SenadoClient._int(vot, "NO"),
                "abstentions": SenadoClient._int(vot, "ABSTENCION"),
                "paired": SenadoClient._int(vot, "PAREO"),
                "quorum": BaseCongresoClient._text(vot, "QUORUM"),
                "voting_type": BaseCongresoClient._text(vot, "TIPOVOTACION"),
                "stage": BaseCongresoClient._text(vot, "ETAPA"),
                "detail": [
                    {
                        "legislator_name": BaseCongresoClient._text(v, "PARLAMENTARIO"),
                        "vote": BaseCongresoClient._text(v, "SELECCION"),
                    }
                    for detalle in [vot.find("DETALLE_VOTACION")]
                    if detalle is not None
                    for v in detalle.iter("VOTO")
                ],
            }
            for vot in proyecto.iter("votacion")
        ]

    @staticmethod
    def _parse_votaciones_from_root(root: ET.Element) -> list[dict[str, Any]]:
        return [
            {
                "session": BaseCongresoClient._text(vot, "SESION"),
                "date": SenadoClient._parse_date_dmy(BaseCongresoClient._text(vot, "FECHA")),
                "subject": BaseCongresoClient._text(vot, "TEMA"),
                "votes_for": SenadoClient._int(vot, "SI"),
                "votes_against": SenadoClient._int(vot, "NO"),
                "abstentions": SenadoClient._int(vot, "ABSTENCION"),
                "paired": SenadoClient._int(vot, "PAREO"),
                "quorum": BaseCongresoClient._text(vot, "QUORUM"),
                "voting_type": BaseCongresoClient._text(vot, "TIPOVOTACION"),
                "stage": BaseCongresoClient._text(vot, "ETAPA"),
                "detail": [
                    {
                        "legislator_name": BaseCongresoClient._text(v, "PARLAMENTARIO"),
                        "vote": BaseCongresoClient._text(v, "SELECCION"),
                    }
                    for detalle in [vot.find("DETALLE_VOTACION")]
                    if detalle is not None
                    for v in detalle.iter("VOTO")
                ],
            }
            for vot in root.iter("votacion")
        ]

    @staticmethod
    def _parse_informes(proyecto: ET.Element) -> list[dict[str, Any]]:
        return [
            {
                "date": SenadoClient._parse_date_dmy(BaseCongresoClient._text(inf, "FECHAINFORME")),
                "procedure": BaseCongresoClient._text(inf, "TRAMITE"),
                "stage": BaseCongresoClient._text(inf, "ETAPA"),
                "url": BaseCongresoClient._text(inf, "LINK_INFORME"),
            }
            for inf in proyecto.iter("informe")
        ]

    @staticmethod
    def _parse_comparados(proyecto: ET.Element) -> list[dict[str, Any]]:
        return [
            {"text": BaseCongresoClient._text(comp, "COMPARADO"), "url": BaseCongresoClient._text(comp, "LINK_COMPARADO")}
            for comp in proyecto.iter("comparado")
        ]

    @staticmethod
    def _parse_oficios(proyecto: ET.Element) -> list[dict[str, Any]]:
        return [
            {
                "number": BaseCongresoClient._text(ofi, "NUMERO"),
                "date": SenadoClient._parse_date_dmy(BaseCongresoClient._text(ofi, "FECHA")),
                "procedure": BaseCongresoClient._text(ofi, "TRAMITE"),
                "stage": BaseCongresoClient._text(ofi, "ETAPA"),
                "type": BaseCongresoClient._text(ofi, "TIPO"),
                "chamber": BaseCongresoClient._text(ofi, "CAMARA"),
                "description": BaseCongresoClient._text(ofi, "DESCRIPCION"),
                "url": BaseCongresoClient._text(ofi, "LINK_OFICIO"),
            }
            for ofi in proyecto.iter("oficio")
        ]

    @staticmethod
    def _int(element: ET.Element | None, tag: str) -> int:
        if element is None:
            return 0
        child = element.find(tag)
        if child is not None and child.text:
            try:
                return int(child.text.strip())
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _parse_date_dmy(date_str: str | None) -> str | None:
        if not date_str or date_str.strip() in {"", "/"}:
            return None
        import re

        match = re.match(r"(\d{2})/(\d{2})/(\d{4})", date_str.strip())
        if match:
            return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
        return None