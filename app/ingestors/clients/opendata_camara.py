import logging
import re
from typing import Any
from xml.etree import ElementTree as ET

from app.core.config import settings
from app.ingestors.clients.base import BaseCongresoClient

logger = logging.getLogger(__name__)

NS = "http://opendata.camara.cl/camaradiputados/v1"
NS_BRACE = f"{{{NS}}}"

# "Boletín N° 15936-18" or "Boletín N°15936-18" or "Boletines N° 15936-18, ..."
# We take the first match — joint-bulletin votes link to the leftmost bulletin
# (existing limitation, see ADR-0013).
_BULLETIN_RE = re.compile(r"Bolet[íi]n(?:es)?\s*N[°º]\s*(\d+-\d+)")


def parse_bulletin_from_description(description: str | None) -> str | None:
    """Extract the first ``NNNN-NN`` bulletin from a free-text Descripcion."""
    if not description:
        return None
    match = _BULLETIN_RE.search(description)
    return match.group(1) if match else None


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
        except ValueError, TypeError:
            return 0

    def _int_attr(
        self, el: ET.Element | None, tag: str, attr: str = "Valor"
    ) -> int | None:
        value = self._attr(el, tag, attr)
        try:
            return int(value)
        except TypeError, ValueError:
            return None

    def _parse_dt(self, value: str) -> str | None:
        if not value:
            return None
        import re

        match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
        if match:
            return match.group(1)
        return None

    def _parse_dt_with_time(self, value: str) -> str | None:
        """Like :meth:`_parse_dt` but preserves ``HH:MM:SS`` when present.

        Upstream ``Fecha`` arrives as naive Chile wall-clock (e.g.
        ``2026-06-10T13:16:55``). The voting pipeline needs the time, while
        date-only fields elsewhere stay on :meth:`_parse_dt`. Returns the
        full ISO string or the date-only form as a graceful fallback.
        """
        if not value:
            return None
        import re

        match = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", value)
        if match:
            return f"{match.group(1)}T{match.group(2)}"
        match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
        if match:
            return match.group(1)
        return None

    def get_diputados_periodo_actual(self) -> list[dict[str, Any]]:
        root = self._get_xml("WSDiputado.asmx/retornarDiputadosPeriodoActual")
        periodos = list(self._iter(root, "DiputadoPeriodo"))
        if periodos:
            results = [self._parse_diputado_periodo(periodo) for periodo in periodos]
        else:
            results = [
                self._parse_diputado(dip) for dip in self._iter(root, "Diputado")
            ]
        logger.info("Fetched %d deputies (current period, opendata)", len(results))
        return results

    def get_all_diputados(self) -> list[dict[str, Any]]:
        """All historical deputies with full militancia history (ADR-0015).

        Hits ``retornarDiputados`` (note: no ``PeriodoActual`` suffix), which
        returns every person who has ever served as a deputy along with their
        complete ``Militancias`` list. Used as the deputy-side roster for the
        historical term backfill: each row produces one ``Legislator`` (merged
        with senado.cl history when the same person is in both lists) and one
        :class:`LegislatorTerm` per militancia, all carrying ``camara:{Id}``
        as the chamber bridge.
        """
        root = self._get_xml("WSDiputado.asmx/retornarDiputados")
        results = [self._parse_diputado(dip) for dip in self._iter(root, "Diputado")]
        logger.info("Fetched %d historical deputies (opendata)", len(results))
        return results

    def _parse_diputado_periodo(self, periodo: ET.Element) -> dict[str, Any]:
        diputado = self._find(periodo, "Diputado")
        distrito = self._find(periodo, "Distrito")
        payload = self._parse_diputado(diputado) if diputado is not None else {}
        payload.update(
            {
                "period_start_date": self._parse_dt(self._txt(periodo, "FechaInicio")),
                "period_end_date": self._parse_dt(self._txt(periodo, "FechaTermino")),
                "district_number": self._int_val(distrito, "Numero")
                if distrito is not None
                else 0,
                "district_communes": [
                    {
                        "number": self._int_val(comuna, "Numero"),
                        "name": self._txt(comuna, "Nombre"),
                    }
                    for comuna in self._iter(distrito, "Comuna")
                ]
                if distrito is not None
                else [],
            }
        )
        return payload

    def _parse_diputado(self, dip: ET.Element) -> dict[str, Any]:
        militancias = [
            {
                "start_date": self._parse_dt(self._txt(mil, "FechaInicio")),
                "end_date": self._parse_dt(self._txt(mil, "FechaTermino")),
                "party_id": self._txt(partido, "Id") if partido is not None else "",
                "party_name": self._txt(partido, "Nombre")
                if partido is not None
                else "",
                "party_alias": self._txt(partido, "Alias")
                if partido is not None
                else "",
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

    def get_comisiones_vigentes(self) -> list[dict[str, Any]]:
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

    def get_comision(self, comision_id: int) -> dict[str, Any] | None:
        root = self._get_xml(
            "WSComision.asmx/retornarComision",
            params={"prmComisionID": str(comision_id)},
        )
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
                "last_name_father": self._txt(dip, "ApellidoPaterno")
                if dip is not None
                else "",
                "last_name_mother": self._txt(dip, "ApellidoMaterno")
                if dip is not None
                else "",
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
                "first_name": self._txt(presidente, "Nombre")
                if presidente is not None
                else "",
                "last_name_father": self._txt(presidente, "ApellidoPaterno")
                if presidente is not None
                else "",
            },
            "members": integrantes,
        }

    def get_legislatura_actual(self) -> dict | None:
        root = self._get_xml("WSLegislativo.asmx/retornarLegislaturaActual")
        return self._parse_legislatura(root)

    def get_legislaturas(self) -> list[dict[str, Any]]:
        root = self._get_xml("WSLegislativo.asmx/retornarLegislaturas")
        legislaturas: list[dict[str, Any]] = []
        for leg in self._iter(root, "Legislatura"):
            parsed = self._parse_legislatura(leg)
            if parsed is not None:
                legislaturas.append(parsed)
        return legislaturas

    def get_periodos_legislativos(self) -> list[dict[str, Any]]:
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

    def _parse_legislatura(self, el: ET.Element) -> dict[str, Any] | None:
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

    def get_regiones(self) -> list[dict[str, Any]]:
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
                            {
                                "number": self._int_val(com, "Numero"),
                                "name": self._txt(com, "Nombre"),
                            }
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

    def get_distritos(self) -> list[dict[str, Any]]:
        root = self._get_xml("WSComun.asmx/retornarDistritos")
        distritos = [
            {
                "number": self._int_val(dist, "Numero"),
                "communes": [
                    {
                        "number": self._int_val(com, "Numero"),
                        "name": self._txt(com, "Nombre"),
                    }
                    for com in self._iter(dist, "Comuna")
                ],
            }
            for dist in self._iter(root, "Distrito")
        ]
        logger.info("Fetched %d districts (opendata)", len(distritos))
        return distritos

    def get_materias(self) -> list[dict[str, Any]]:
        root = self._get_xml("WSLegislativo.asmx/retornarMaterias")
        materias = [
            {
                "name": self._txt(materia, "Nombre"),
                "source_id": self._int_val(materia, "Id") or None,
            }
            for materia in self._iter(root, "Materia")
            if self._txt(materia, "Nombre")
        ]
        logger.info("Fetched %d topics (opendata)", len(materias))
        return materias

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

    def _parse_chamber_vote_summary(self, voting: ET.Element) -> dict[str, Any]:
        return {
            "id": self._int_val(voting, "Id"),
            "description": self._txt(voting, "Descripcion"),
            "date": self._parse_dt_with_time(self._txt(voting, "Fecha")),
            "votes_for": self._int_val(voting, "TotalSi"),
            "votes_against": self._int_val(voting, "TotalNo"),
            "abstentions": self._int_val(voting, "TotalAbstencion"),
            "dispensed_count": self._int_val(voting, "TotalDispensado"),
            "quorum": self._txt(voting, "Quorum"),
            "quorum_code": self._int_attr(voting, "Quorum", "Valor"),
            "result": self._txt(voting, "Resultado"),
            "result_code": self._int_attr(voting, "Resultado", "Valor"),
            "type": self._txt(voting, "Tipo"),
            "type_code": self._int_attr(voting, "Tipo", "Valor"),
            "voting_type": self._txt(voting, "TipoVotacionProyectoLey"),
            "voting_type_code": self._int_attr(
                voting, "TipoVotacionProyectoLey", "Valor"
            ),
            "article_text": self._txt(voting, "Articulo"),
            "constitutional_procedure": self._txt(voting, "TramiteConstitucional"),
            "constitutional_procedure_id": self._int_attr(
                voting, "TramiteConstitucional", "Id"
            ),
            "regulatory_procedure": self._txt(voting, "TramiteReglamentario"),
            "regulatory_procedure_id": self._int_attr(
                voting, "TramiteReglamentario", "Id"
            ),
        }

    def _parse_bill_detail(self, proyecto: ET.Element) -> dict[str, Any]:
        payload = self._parse_proyecto_ley(proyecto)
        payload["sponsoring_ministries"] = [
            {
                "id": self._int_val(ministry, "Id") or None,
                "name": self._txt(ministry, "Nombre") or None,
            }
            for ministry in self._iter(proyecto, "Ministerio")
        ]
        payload["chamber_votes"] = [
            self._parse_chamber_vote_summary(voting)
            for voting in self._iter(proyecto, "VotacionProyectoLey")
        ]
        return payload

    def get_bill_detail(self, bulletin: str) -> dict[str, Any] | None:
        root = self._get_xml(
            "WSLegislativo.asmx/retornarProyectoLey",
            params={"prmNumeroBoletin": bulletin},
        )
        proyecto = root
        if proyecto.tag not in (f"{NS_BRACE}ProyectoLey", "ProyectoLey"):
            found = self._find(root, "ProyectoLey")
            if found is None:
                return None
            proyecto = found

        detail = self._parse_bill_detail(proyecto)
        if not detail.get("bulletin_number"):
            return None

        logger.info("Fetched bill detail for bulletin %s (opendata)", bulletin)
        return detail

    def _parse_vote_detail(self, voting: ET.Element) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self._int_val(voting, "Id"),
            "description": self._txt(voting, "Descripcion"),
            "date": self._parse_dt_with_time(self._txt(voting, "Fecha")),
            "votes_for": self._int_val(voting, "TotalSi"),
            "votes_against": self._int_val(voting, "TotalNo"),
            "abstentions": self._int_val(voting, "TotalAbstencion"),
            "dispensed_count": self._int_val(voting, "TotalDispensado"),
            "quorum": self._txt(voting, "Quorum"),
            "quorum_code": self._int_attr(voting, "Quorum", "Valor"),
            "result": self._txt(voting, "Resultado"),
            "result_code": self._int_attr(voting, "Resultado", "Valor"),
            "type": self._txt(voting, "Tipo"),
            "type_code": self._int_attr(voting, "Tipo", "Valor"),
        }
        payload["individual_votes"] = [
            {
                "deputy_id": self._int_val(deputy, "Id") or None,
                "first_name": self._txt(deputy, "Nombre"),
                "last_name_father": self._txt(deputy, "ApellidoPaterno"),
                "last_name_mother": self._txt(deputy, "ApellidoMaterno"),
                "vote": self._txt(vote, "OpcionVoto"),
                "vote_code": self._int_attr(vote, "OpcionVoto", "Valor"),
            }
            for vote in self._iter(voting, "Voto")
            for deputy in [self._find(vote, "Diputado")]
            if deputy is not None
        ]
        return payload

    def get_voting_detail(self, voting_id: int) -> dict[str, Any] | None:
        root = self._get_xml(
            "WSLegislativo.asmx/retornarVotacionDetalle",
            params={"prmVotacionId": str(voting_id)},
        )
        voting = root
        if voting.tag not in (f"{NS_BRACE}Votacion", "Votacion"):
            found = self._find(root, "Votacion")
            if found is None:
                return None
            voting = found

        detail = self._parse_vote_detail(voting)
        if not detail.get("id"):
            return None

        logger.info("Fetched vote detail for voting id %s (opendata)", voting_id)
        return detail

    def _parse_bulk_chamber_vote_summary(self, voting: ET.Element) -> dict[str, Any]:
        """Parse the light ``<Votacion>`` shape returned by ``retornarVotacionesXAnno``.

        This shape lacks ``TipoVotacionProyectoLey``, ``Articulo``, and the
        ``Tramite*`` fields — those come from the per-bulletin enrichment via
        ``retornarVotacionesXProyectoLey``.
        """
        return {
            "id": self._int_val(voting, "Id"),
            "description": self._txt(voting, "Descripcion"),
            "date": self._parse_dt_with_time(self._txt(voting, "Fecha")),
            "votes_for": self._int_val(voting, "TotalSi"),
            "votes_against": self._int_val(voting, "TotalNo"),
            "abstentions": self._int_val(voting, "TotalAbstencion"),
            "dispensed_count": self._int_val(voting, "TotalDispensado"),
            "quorum": self._txt(voting, "Quorum"),
            "quorum_code": self._int_attr(voting, "Quorum", "Valor"),
            "result": self._txt(voting, "Resultado"),
            "result_code": self._int_attr(voting, "Resultado", "Valor"),
            "type": self._txt(voting, "Tipo"),
            "type_code": self._int_attr(voting, "Tipo", "Valor"),
        }

    def get_votes_by_year(self, year: int) -> list[dict[str, Any]]:
        """Bulk year-keyed Chamber-vote feed (ADR-0013).

        Returns light per-vote summaries in upstream desc-by-``Id`` order.
        Each row's ``description`` contains the bulletin as free text — use
        :func:`parse_bulletin_from_description` to extract it.
        """
        root = self._get_xml(
            "WSLegislativo.asmx/retornarVotacionesXAnno",
            params={"prmAnno": str(year)},
        )
        results = [
            self._parse_bulk_chamber_vote_summary(voting)
            for voting in self._iter(root, "Votacion")
        ]
        logger.info(
            "Fetched %d chamber votes for year %d (opendata)", len(results), year
        )
        return results

    def get_chamber_votes_for_bulletin(self, bulletin: str) -> list[dict[str, Any]]:
        """Per-bulletin enrichment feed used by the chamber-votes ingest (ADR-0013).

        Returns rich ``<VotacionProyectoLey>`` summaries (carries
        ``TipoVotacionProyectoLey``, ``Articulo``, ``Tramite*``) without the
        full bill detail body that ``retornarProyectoLey`` returns.
        """
        root = self._get_xml(
            "WSLegislativo.asmx/retornarVotacionesXProyectoLey",
            params={"prmNumeroBoletin": bulletin},
        )
        results = [
            self._parse_chamber_vote_summary(voting)
            for voting in self._iter(root, "VotacionProyectoLey")
        ]
        logger.info(
            "Fetched %d chamber votes for bulletin %s (opendata)",
            len(results),
            bulletin,
        )
        return results

    def get_mensajes_x_anno(self, anno: int) -> list[dict]:
        root = self._get_xml(
            "WSLegislativo.asmx/retornarMensajesXAnno", params={"prmAnno": str(anno)}
        )
        results = [
            self._parse_proyecto_ley(proy) for proy in self._iter(root, "ProyectoLey")
        ]
        logger.info("Fetched %d mensajes for year %d (opendata)", len(results), anno)
        return results

    def get_mociones_x_anno(self, anno: int) -> list[dict]:
        root = self._get_xml(
            "WSLegislativo.asmx/retornarMocionesXAnno", params={"prmAnno": str(anno)}
        )
        results = [
            self._parse_proyecto_ley(proy) for proy in self._iter(root, "ProyectoLey")
        ]
        logger.info("Fetched %d mociones for year %d (opendata)", len(results), anno)
        return results
