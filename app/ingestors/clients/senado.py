import datetime
import logging
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient

logger = logging.getLogger(__name__)


class SenadoClient(BaseCongresoClient):
    BASE_URL = settings.ingestor_base_url_senado

    def get_senadores_vigentes(self) -> list[dict]:
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

    def get_comisiones(self) -> list[dict]:
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

    def get_bill_by_bulletin(self, bulletin: str) -> dict | None:
        boletin_num = bulletin.split("-")[0]
        root = self._get_xml("tramitacion.php", params={"boletin": boletin_num})
        proyecto = root.find(".//proyecto")
        if proyecto is None:
            logger.warning("No project found for bulletin %s", bulletin)
            return None

        desc = proyecto.find("descripcion")
        bill = {
            "bulletin": self._text(desc, "boletin"),
            "title": self._text(desc, "titulo"),
            "entry_date": self._parse_date_dmy(self._text(desc, "fecha_ingreso")),
            "initiative": self._text(desc, "iniciativa"),
            "origin_chamber": self._text(desc, "camara_origen"),
            "current_urgency": self._text(desc, "urgencia_actual"),
            "stage": self._text(desc, "etapa"),
            "substage": self._text(desc, "subetapa"),
            "law_number": self._text(desc, "leynro"),
            "publication_date": self._parse_date_dmy(self._text(desc, "diariooficial")),
            "status": self._text(desc, "estado"),
            "message_url": self._text(desc, "link_mensaje_mocion"),
        }
        bill["authors"] = self._parse_authors(proyecto)
        bill["tramitaciones"] = self._parse_tramitaciones(proyecto)
        bill["votaciones"] = self._parse_votaciones(proyecto)
        bill["informes"] = self._parse_informes(proyecto)
        bill["comparados"] = self._parse_comparados(proyecto)
        bill["oficios"] = self._parse_oficios(proyecto)
        bill["materias"] = [self._text(m, "DESCRIPCION") for m in proyecto.iter("materia")]
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

    def get_votes_by_bulletin(self, bulletin: str) -> list[dict]:
        boletin_num = bulletin.split("-")[0]
        root = self._get_xml("votaciones.php", params={"boletin": boletin_num})
        return self._parse_votaciones_from_root(root)

    def _parse_authors(self, proyecto: ET.Element) -> list[dict]:
        return [{"legislator": self._text(auth, "PARLAMENTARIO")} for auth in proyecto.iter("autor")]

    def _parse_tramitaciones(self, proyecto: ET.Element) -> list[dict]:
        return [
            {
                "session": self._text(tram, "SESION"),
                "date": self._parse_date_dmy(self._text(tram, "FECHA")),
                "description": self._text(tram, "DESCRIPCIONTRAMITE"),
                "stage": self._text(tram, "ETAPDESCRIPCION"),
                "chamber": self._text(tram, "CAMARATRAMITE"),
            }
            for tram in proyecto.iter("tramite")
        ]

    def _parse_votaciones(self, proyecto: ET.Element) -> list[dict]:
        return [
            {
                "session": self._text(vot, "SESION"),
                "date": self._parse_date_dmy(self._text(vot, "FECHA")),
                "subject": self._text(vot, "TEMA"),
                "votes_for": self._int(vot, "SI"),
                "votes_against": self._int(vot, "NO"),
                "abstentions": self._int(vot, "ABSTENCION"),
                "paired": self._int(vot, "PAREO"),
                "quorum": self._text(vot, "QUORUM"),
                "voting_type": self._text(vot, "TIPOVOTACION"),
                "stage": self._text(vot, "ETAPA"),
                "detail": [
                    {
                        "legislator_name": self._text(v, "PARLAMENTARIO"),
                        "vote": self._text(v, "SELECCION"),
                    }
                    for detalle in [vot.find("DETALLE_VOTACION")]
                    if detalle is not None
                    for v in detalle.iter("VOTO")
                ],
            }
            for vot in proyecto.iter("votacion")
        ]

    def _parse_votaciones_from_root(self, root: ET.Element) -> list[dict]:
        return [
            {
                "session": self._text(vot, "SESION"),
                "date": self._parse_date_dmy(self._text(vot, "FECHA")),
                "subject": self._text(vot, "TEMA"),
                "votes_for": self._int(vot, "SI"),
                "votes_against": self._int(vot, "NO"),
                "abstentions": self._int(vot, "ABSTENCION"),
                "paired": self._int(vot, "PAREO"),
                "quorum": self._text(vot, "QUORUM"),
                "voting_type": self._text(vot, "TIPOVOTACION"),
                "stage": self._text(vot, "ETAPA"),
                "detail": [
                    {
                        "legislator_name": self._text(v, "PARLAMENTARIO"),
                        "vote": self._text(v, "SELECCION"),
                    }
                    for detalle in [vot.find("DETALLE_VOTACION")]
                    if detalle is not None
                    for v in detalle.iter("VOTO")
                ],
            }
            for vot in root.iter("votacion")
        ]

    def _parse_informes(self, proyecto: ET.Element) -> list[dict]:
        return [
            {
                "date": self._parse_date_dmy(self._text(inf, "FECHAINFORME")),
                "procedure": self._text(inf, "TRAMITE"),
                "stage": self._text(inf, "ETAPA"),
                "url": self._text(inf, "LINK_INFORME"),
            }
            for inf in proyecto.iter("informe")
        ]

    def _parse_comparados(self, proyecto: ET.Element) -> list[dict]:
        return [
            {"text": self._text(comp, "COMPARADO"), "url": self._text(comp, "LINK_COMPARADO")}
            for comp in proyecto.iter("comparado")
        ]

    def _parse_oficios(self, proyecto: ET.Element) -> list[dict]:
        return [
            {
                "number": self._text(ofi, "NUMERO"),
                "date": self._parse_date_dmy(self._text(ofi, "FECHA")),
                "procedure": self._text(ofi, "TRAMITE"),
                "stage": self._text(ofi, "ETAPA"),
                "type": self._text(ofi, "TIPO"),
                "chamber": self._text(ofi, "CAMARA"),
                "description": self._text(ofi, "DESCRIPCION"),
                "url": self._text(ofi, "LINK_OFICIO"),
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