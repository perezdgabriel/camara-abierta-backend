import logging
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient

logger = logging.getLogger(__name__)

NS = "http://opendata.camara.cl/camaradiputados/v1"
NS_BRACE = f"{{{NS}}}"


class OpenDataCamaraClient(BaseCongresoClient):
    BASE_URL = settings.ingestor_base_url_opendata_camara

    def _iter(self, el: ET.Element, tag: str):
        yield from el.iter(f"{NS_BRACE}{tag}")
        yield from el.iter(tag)

    def _find(self, el: ET.Element, tag: str) -> ET.Element | None:
        found = el.find(f"{NS_BRACE}{tag}")
        if found is None:
            found = el.find(tag)
        return found

    def _txt(self, el: ET.Element | None, tag: str) -> str:
        if el is None:
            return ""
        child = self._find(el, tag)
        if child is not None and child.text:
            return child.text.strip()
        return ""

    def _attr(self, el: ET.Element | None, tag: str, attr: str = "Codigo") -> str:
        if el is None:
            return ""
        child = self._find(el, tag)
        if child is not None:
            return child.get(attr, "")
        return ""

    def _int_val(self, el: ET.Element | None, tag: str) -> int:
        value = self._txt(el, tag)
        try:
            return int(value)
        except (ValueError, TypeError):
            return 0

    def _parse_dt(self, value: str) -> str | None:
        if not value:
            return None
        import re

        match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
        if match:
            return match.group(1)
        return None

    def get_diputados_periodo_actual(self) -> list[dict]:
        root = self._get_xml("WSDiputado.asmx/retornarDiputadosPeriodoActual")
        results = [self._parse_diputado(dip) for dip in self._iter(root, "Diputado")]
        logger.info("Fetched %d deputies (current period, opendata)", len(results))
        return results

    def _parse_diputado(self, dip: ET.Element) -> dict:
        militancias = [
            {
                "start_date": self._parse_dt(self._txt(mil, "FechaInicio")),
                "end_date": self._parse_dt(self._txt(mil, "FechaTermino")),
                "party_id": self._txt(partido, "Id") if partido is not None else "",
                "party_name": self._txt(partido, "Nombre") if partido is not None else "",
                "party_alias": self._txt(partido, "Alias") if partido is not None else "",
            }
            for mil in self._iter(dip, "Militancia")
            for partido in [self._find(mil, "Partido")]
        ]
        return {
            "id": self._int_val(dip, "Id"),
            "first_name": self._txt(dip, "Nombre"),
            "second_name": self._txt(dip, "Nombre2"),
            "last_name_father": self._txt(dip, "ApellidoPaterno"),
            "last_name_mother": self._txt(dip, "ApellidoMaterno"),
            "birth_date": self._parse_dt(self._txt(dip, "FechaNacimiento")),
            "gender": self._txt(dip, "Sexo"),
            "gender_code": self._attr(dip, "Sexo"),
            "militancias": militancias,
        }

    def get_comisiones_vigentes(self) -> list[dict]:
        root = self._get_xml("WSComision.asmx/retornarComisionesVigentes")
        comisiones = [
            {
                "id": self._int_val(com, "Id"),
                "name": self._txt(com, "Nombre"),
                "web_name": self._txt(com, "NombreWeb"),
                "alias": self._txt(com, "Alias"),
                "type": self._txt(com, "Tipo"),
                "type_code": self._attr(com, "Tipo"),
                "start_date": self._parse_dt(self._txt(com, "FechaInicio")),
                "end_date": self._parse_dt(self._txt(com, "FechaTermino")),
                "email": self._txt(com, "Correo"),
                "phone": self._txt(com, "Telefono"),
            }
            for com in self._iter(root, "Comision")
        ]
        logger.info("Fetched %d committees (opendata)", len(comisiones))
        return comisiones

    def get_comision(self, comision_id: int) -> dict | None:
        root = self._get_xml("WSComision.asmx/retornarComision", params={"prmComisionID": str(comision_id)})
        com = root
        if com.tag not in (f"{NS_BRACE}Comision", "Comision"):
            found = self._find(root, "Comision")
            if found is not None:
                com = found

        integrantes = [
            {
                "start_date": self._parse_dt(self._txt(di, "FechaInicio")),
                "end_date": self._parse_dt(self._txt(di, "FechaTermino")),
                "diputado_id": self._int_val(dip, "Id") if dip is not None else 0,
                "first_name": self._txt(dip, "Nombre") if dip is not None else "",
                "last_name_father": self._txt(dip, "ApellidoPaterno") if dip is not None else "",
                "last_name_mother": self._txt(dip, "ApellidoMaterno") if dip is not None else "",
            }
            for di in self._iter(com, "DiputadoIntegrante")
            for dip in [self._find(di, "Diputado")]
        ]

        presidente = self._find(com, "Presidente")
        return {
            "id": self._int_val(com, "Id"),
            "name": self._txt(com, "Nombre"),
            "web_name": self._txt(com, "NombreWeb"),
            "alias": self._txt(com, "Alias"),
            "type": self._txt(com, "Tipo"),
            "type_code": self._attr(com, "Tipo"),
            "start_date": self._parse_dt(self._txt(com, "FechaInicio")),
            "end_date": self._parse_dt(self._txt(com, "FechaTermino")),
            "email": self._txt(com, "Correo"),
            "phone": self._txt(com, "Telefono"),
            "president": {
                "id": self._int_val(presidente, "Id") if presidente is not None else 0,
                "first_name": self._txt(presidente, "Nombre") if presidente is not None else "",
                "last_name_father": self._txt(presidente, "ApellidoPaterno") if presidente is not None else "",
            },
            "members": integrantes,
        }

    def get_legislatura_actual(self) -> dict | None:
        root = self._get_xml("WSLegislativo.asmx/retornarLegislaturaActual")
        return self._parse_legislatura(root)

    def get_legislaturas(self) -> list[dict]:
        root = self._get_xml("WSLegislativo.asmx/retornarLegislaturas")
        return [self._parse_legislatura(leg) for leg in self._iter(root, "Legislatura") if self._parse_legislatura(leg)]

    def get_periodos_legislativos(self) -> list[dict]:
        root = self._get_xml("WSLegislativo.asmx/retornarPeriodosLegislativos")
        return [
            {
                "id": self._int_val(per, "Id"),
                "name": self._txt(per, "Nombre"),
                "start_date": self._parse_dt(self._txt(per, "FechaInicio")),
                "end_date": self._parse_dt(self._txt(per, "FechaTermino")),
            }
            for per in self._iter(root, "PeriodoLegislativo")
        ]

    def _parse_legislatura(self, el: ET.Element) -> dict | None:
        id_val = self._int_val(el, "Id")
        if not id_val and el.tag not in (f"{NS_BRACE}Legislatura", "Legislatura"):
            return None
        return {
            "id": id_val,
            "number": self._int_val(el, "Numero"),
            "start_date": self._parse_dt(self._txt(el, "FechaInicio")),
            "end_date": self._parse_dt(self._txt(el, "FechaTermino")),
            "type": self._txt(el, "Tipo"),
            "type_code": self._attr(el, "Tipo"),
        }

    def get_regiones(self) -> list[dict]:
        root = self._get_xml("WSComun.asmx/retornarRegiones")
        regiones = [
            {
                "number": self._int_val(reg, "Numero"),
                "roman_number": self._txt(reg, "NumeroRomano"),
                "name": self._txt(reg, "Nombre"),
                "provinces": [
                    {
                        "number": self._int_val(prov, "Numero"),
                        "name": self._txt(prov, "Nombre"),
                        "communes": [
                            {"number": self._int_val(com, "Numero"), "name": self._txt(com, "Nombre")}
                            for com in self._iter(prov, "Comuna")
                        ],
                    }
                    for prov in self._iter(reg, "Provincia")
                ],
            }
            for reg in self._iter(root, "Region")
        ]
        logger.info("Fetched %d regions (opendata)", len(regiones))
        return regiones

    def get_distritos(self) -> list[dict]:
        root = self._get_xml("WSComun.asmx/retornarDistritos")
        distritos = [
            {
                "number": self._int_val(dist, "Numero"),
                "communes": [
                    {"number": self._int_val(com, "Numero"), "name": self._txt(com, "Nombre")}
                    for com in self._iter(dist, "Comuna")
                ],
            }
            for dist in self._iter(root, "Distrito")
        ]
        logger.info("Fetched %d districts (opendata)", len(distritos))
        return distritos

    def _parse_proyecto_ley(self, proyecto: ET.Element) -> dict:
        return {
            "id": self._int_val(proyecto, "Id"),
            "bulletin_number": self._txt(proyecto, "NumeroBoletin"),
            "title": self._txt(proyecto, "Nombre"),
            "entry_date": self._parse_dt(self._txt(proyecto, "FechaIngreso")),
            "initiative_type": self._txt(proyecto, "TipoIniciativa"),
            "initiative_type_code": self._attr(proyecto, "TipoIniciativa", "Valor"),
            "origin_chamber": self._txt(proyecto, "CamaraOrigen"),
            "origin_chamber_code": self._attr(proyecto, "CamaraOrigen", "Valor"),
            "admissible": self._txt(proyecto, "Admisible") == "true",
        }

    def get_mensajes_x_anno(self, anno: int) -> list[dict]:
        root = self._get_xml("WSLegislativo.asmx/retornarMensajesXAnno", params={"prmAnno": str(anno)})
        results = [self._parse_proyecto_ley(proy) for proy in self._iter(root, "ProyectoLey")]
        logger.info("Fetched %d mensajes for year %d (opendata)", len(results), anno)
        return results

    def get_mociones_x_anno(self, anno: int) -> list[dict]:
        root = self._get_xml("WSLegislativo.asmx/retornarMocionesXAnno", params={"prmAnno": str(anno)})
        results = [self._parse_proyecto_ley(proy) for proy in self._iter(root, "ProyectoLey")]
        logger.info("Fetched %d mociones for year %d (opendata)", len(results), anno)
        return results