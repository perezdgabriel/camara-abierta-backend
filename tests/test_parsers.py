import json
from pathlib import Path
from xml.etree import ElementTree as ET

from app.ingestors.clients.opendata_camara import OpenDataCamaraClient
from app.ingestors.clients.senado import SenadoClient
from app.ingestors.parsers.bills import BillParser
from app.ingestors.parsers.legislators import LegislatorParser
from app.ingestors.parsers.legislature import LegislatureParser
from app.ingestors.parsers.votes import VoteParser
from app.models.enums import (
    BillOrigin,
    BillStatus,
    ChamberType,
    StageType,
    UrgencyType,
    VoteChoice,
    VotingResult,
    VotingType,
)


def test_bill_parser_maps_canonical_enums_and_discards_blank_authors():
    payload = BillParser.parse_bill(
        {
            "bulletin": "555-06",
            "title": "  Proyecto de prueba  ",
            "entry_date": "2026-05-10",
            "initiative": "Mensaje",
            "origin_chamber": "Senado",
            "status": "Publicado",
            "current_urgency": "Discusión inmediata",
            "authors": [
                {"legislator": "Ada Demo"},
                {"legislator": "  "},
            ],
            "tramitaciones": [
                {
                    "stage": "Primer trámite constitucional",
                    "date": "2026-05-11",
                    "chamber": "Senado",
                    "description": "Ingreso",
                    "session": "1",
                }
            ],
            "informes": [],
            "comparados": [],
            "oficios": [],
            "votaciones": [],
        }
    )

    assert payload["origin_type"] is BillOrigin.EXECUTIVE
    assert payload["_origin_chamber_type"] is ChamberType.SENATE
    assert payload["status"] is BillStatus.PUBLISHED
    assert payload["_current_urgency_type"] is UrgencyType.IMMEDIATE
    assert payload["authors"] == [{"name": "Ada Demo"}]
    assert payload["stages"][0]["stage_type"] is StageType.FIRST_CONSTITUTIONAL_TRAMITE
    assert payload["stages"][0]["_chamber_type"] is ChamberType.SENATE
    assert payload["events"] == [
        {
            "event_date": "2026-05-11",
            "title": "Ingreso",
            "description": "Primer trámite constitucional",
            "_chamber_type": ChamberType.SENATE,
        }
    ]


def test_vote_parser_maps_votes_and_result_to_canonical_enums():
    payload = VoteParser.parse_senate_vote(
        {
            "voting_type": "Discusión general",
            "session": "42",
            "stage": "Primer trámite constitucional",
            "subject": "Votación del proyecto",
            "date": "2026-05-12",
            "votes_for": 20,
            "votes_against": 10,
            "abstentions": 1,
            "paired": 2,
            "quorum": "simple",
            "detail": [
                {"legislator_name": "Ada Demo", "vote": "Sí"},
                {"legislator_name": "Beto Demo", "vote": "Desconocido"},
            ],
        },
        bulletin="555-06",
    )

    assert payload["bcn_id"] == "senado:vot:555-06:42"
    assert payload["_chamber_type"] is ChamberType.SENATE
    assert payload["voting_type"] is VotingType.GENERAL
    assert payload["result"] is VotingResult.APPROVED
    assert payload["session_ref"] == "42"
    assert payload["stage_label"] == "Primer trámite constitucional"
    assert payload["paired_count"] == 2
    assert payload["individual_votes"][0]["vote"] is VoteChoice.FOR
    assert payload["individual_votes"][1]["vote"] is VoteChoice.NO_VOTE


def test_bill_parser_maps_opendata_enrichment_to_sponsoring_ministries_and_votes():
    payload = BillParser.parse_opendata_enrichment(
        {
            "sponsoring_ministries": [
                {"id": 12, "name": "Ministerio de Hacienda"},
                {"id": None, "name": "  Ministerio Secretaría General  "},
            ],
            "chamber_votes": [{"id": 88980}, {"id": 88981}],
        }
    )

    assert payload["sponsoring_ministries"] == [
        {"source_id": 12, "name": "Ministerio de Hacienda"},
        {"source_id": None, "name": "Ministerio Secretaría General"},
    ]
    assert payload["_camara_votaciones"] == [{"id": 88980}, {"id": 88981}]


def test_vote_parser_maps_chamber_votes_and_metadata_to_canonical_payload():
    payload = VoteParser.parse_chamber_vote(
        {
            "id": 88980,
            "description": "Boletín N° 18216-05",
            "date": "2026-05-20T14:04:00",
            "votes_for": 68,
            "votes_against": 83,
            "abstentions": 2,
            "dispensed_count": 1,
            "quorum": "Quórum Simple",
            "result": "Rechazado",
            "voting_type": "Discusión particular",
            "article_text": "Artículo 1°",
            "constitutional_procedure_id": 1,
            "constitutional_procedure": "Primer trámite constitucional",
            "regulatory_procedure_id": 2,
            "regulatory_procedure": "Segundo informe",
            "individual_votes": [
                {
                    "deputy_id": 803,
                    "first_name": "René",
                    "last_name_father": "Alinco",
                    "last_name_mother": "Bustos",
                    "vote": "Dispensado",
                    "vote_code": 3,
                }
            ],
        },
        bulletin="18216-05",
    )

    assert payload["bcn_id"] == "camara:vot:88980"
    assert payload["_chamber_type"] is ChamberType.DEPUTIES
    assert payload["bill_bulletin"] == "18216-05"
    assert payload["voting_type"] is VotingType.PARTICULAR
    assert payload["subject"] == "Artículo 1°"
    assert payload["result"] is VotingResult.REJECTED
    assert payload["dispensed_count"] == 1
    assert payload["constitutional_procedure_id"] == 1
    assert payload["regulatory_procedure_label"] == "Segundo informe"
    assert payload["individual_votes"][0]["legislator_external_id"] == "camara:803"
    assert payload["individual_votes"][0]["_legislator_name"] == "René Alinco Bustos"
    assert payload["individual_votes"][0]["vote"] is VoteChoice.DISPENSED


def test_vote_parser_maps_chamber_no_vota_value_to_no_vote_choice():
    payload = VoteParser.parse_chamber_vote(
        {
            "id": 88981,
            "description": "Boletín N° 18216-05",
            "date": "2026-05-20T14:04:00",
            "votes_for": 70,
            "votes_against": 60,
            "abstentions": 0,
            "dispensed_count": 0,
            "result": "Aprobado",
            "individual_votes": [
                {
                    "deputy_id": 901,
                    "first_name": "Camila",
                    "last_name_father": "Test",
                    "last_name_mother": "Case",
                    "vote": "No Vota",
                    "vote_code": 4,
                },
                {
                    "deputy_id": 902,
                    "first_name": "Diego",
                    "last_name_father": "Test",
                    "last_name_mother": "Case",
                    "vote": None,
                    "vote_code": None,
                },
            ],
        },
        bulletin="18216-05",
    )
    assert payload["individual_votes"][0]["vote"] is VoteChoice.NO_VOTE
    assert payload["individual_votes"][1]["vote"] is VoteChoice.NO_VOTE


def test_senado_bill_xml_maps_through_bill_and_vote_parsers():
    root = ET.fromstring(
        """
                <root>
                    <proyecto>
                        <descripcion>
                            <boletin>555-06</boletin>
                            <titulo> Proyecto desde Senado </titulo>
                            <fecha_ingreso>10/05/2026</fecha_ingreso>
                            <iniciativa>Mensaje</iniciativa>
                            <camara_origen>Senado</camara_origen>
                            <urgencia_actual>Discusión inmediata</urgencia_actual>
                            <etapa>Primer trámite constitucional</etapa>
                            <subetapa>Sala</subetapa>
                            <leynro>21600</leynro>
                            <diariooficial>20/05/2026</diariooficial>
                            <estado>Publicado</estado>
                            <link_mensaje_mocion>https://example.com/message</link_mensaje_mocion>
                        </descripcion>
                        <autor>
                            <PARLAMENTARIO>Ada Demo</PARLAMENTARIO>
                        </autor>
                        <materia>
                            <DESCRIPCION> Transparencia </DESCRIPCION>
                        </materia>
                        <tramite>
                            <SESION>12</SESION>
                            <FECHA>11/05/2026</FECHA>
                            <DESCRIPCIONTRAMITE>Ingreso</DESCRIPCIONTRAMITE>
                            <ETAPDESCRIPCION>Primer trámite constitucional</ETAPDESCRIPCION>
                            <CAMARATRAMITE>Senado</CAMARATRAMITE>
                        </tramite>
                        <votacion>
                            <SESION>42</SESION>
                            <FECHA>12/05/2026</FECHA>
                            <TEMA>Votación del proyecto</TEMA>
                            <SI>20</SI>
                            <NO>10</NO>
                            <ABSTENCION>1</ABSTENCION>
                            <PAREO>2</PAREO>
                            <QUORUM>simple</QUORUM>
                            <TIPOVOTACION>Discusión general</TIPOVOTACION>
                            <ETAPA>Primer trámite constitucional</ETAPA>
                            <DETALLE_VOTACION>
                                <VOTO>
                                    <PARLAMENTARIO>Ada Demo</PARLAMENTARIO>
                                    <SELECCION>Sí</SELECCION>
                                </VOTO>
                            </DETALLE_VOTACION>
                        </votacion>
                        <informe>
                            <FECHAINFORME>13/05/2026</FECHAINFORME>
                            <TRAMITE>Primer informe</TRAMITE>
                            <ETAPA>Primer trámite constitucional</ETAPA>
                            <LINK_INFORME>https://example.com/report.pdf</LINK_INFORME>
                        </informe>
                        <comparado>
                            <COMPARADO>Texto comparado</COMPARADO>
                            <LINK_COMPARADO>https://example.com/comparison.pdf</LINK_COMPARADO>
                        </comparado>
                        <oficio>
                            <NUMERO>123</NUMERO>
                            <FECHA>14/05/2026</FECHA>
                            <TRAMITE>Oficio</TRAMITE>
                            <ETAPA>Primer trámite constitucional</ETAPA>
                            <TIPO>Respuesta</TIPO>
                            <CAMARA>Senado</CAMARA>
                            <DESCRIPCION>Oficio de respuesta</DESCRIPCION>
                            <LINK_OFICIO>https://example.com/oficio.pdf</LINK_OFICIO>
                        </oficio>
                    </proyecto>
                </root>
                """
    )

    raw = SenadoClient._parse_bill_xml(root, "555-06")

    assert raw is not None
    assert raw["bulletin"] == "555-06"
    assert raw["entry_date"] == "2026-05-10"
    assert raw["publication_date"] == "2026-05-20"
    assert len(raw["tramitaciones"]) == 1
    assert len(raw["votaciones"]) == 1

    bill_payload = BillParser.parse_bill(raw)

    assert bill_payload["bulletin_number"] == "555-06"
    assert bill_payload["title"] == "Proyecto desde Senado"
    assert bill_payload["origin_type"] is BillOrigin.EXECUTIVE
    assert bill_payload["_origin_chamber_type"] is ChamberType.SENATE
    assert bill_payload["status"] is BillStatus.PUBLISHED
    assert bill_payload["_current_urgency_type"] is UrgencyType.IMMEDIATE
    assert bill_payload["authors"] == [{"name": "Ada Demo"}]
    assert (
        bill_payload["stages"][0]["stage_type"]
        is StageType.FIRST_CONSTITUTIONAL_TRAMITE
    )
    assert bill_payload["stages"][0]["_chamber_type"] is ChamberType.SENATE
    assert bill_payload["events"] == [
        {
            "event_date": "2026-05-11",
            "title": "Ingreso",
            "description": "Primer trámite constitucional",
            "_chamber_type": ChamberType.SENATE,
        }
    ]
    assert len(bill_payload["documents"]) == 3

    vote_payload = VoteParser.parse_senate_vote(
        bill_payload["_votaciones"][0],
        bulletin=bill_payload["bulletin_number"],
    )

    assert vote_payload["bcn_id"] == "senado:vot:555-06:42"
    assert vote_payload["voting_type"] is VotingType.GENERAL
    assert vote_payload["result"] is VotingResult.APPROVED
    assert vote_payload["session_ref"] == "42"
    assert vote_payload["stage_label"] == "Primer trámite constitucional"
    assert vote_payload["paired_count"] == 2
    assert vote_payload["individual_votes"][0]["vote"] is VoteChoice.FOR


def test_opendata_diputado_periodo_parser_preserves_wrapper_metadata():
    periodo = ET.fromstring(
        """
        <DiputadoPeriodo
            xmlns="http://opendata.camara.cl/camaradiputados/v1"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
        >
            <FechaInicio>2026-03-11T00:00:00</FechaInicio>
            <FechaTermino xsi:nil="true" />
            <Diputado>
                <Id>1254</Id>
                <Nombre>Ignacio</Nombre>
                <Nombre2 />
                <ApellidoPaterno>Urcullú</ApellidoPaterno>
                <ApellidoMaterno>Clèment-Lund</ApellidoMaterno>
                <FechaNacimiento>1976-03-07T00:00:00</FechaNacimiento>
                <Sexo Valor="1">Masculino</Sexo>
                <Militancias>
                    <Militancia>
                        <FechaInicio>2026-03-11T00:00:00</FechaInicio>
                        <FechaTermino>2030-03-10T23:59:59</FechaTermino>
                        <Partido>
                            <Id>PREP</Id>
                            <Nombre>Partido Republicano</Nombre>
                            <Alias>PREP</Alias>
                        </Partido>
                    </Militancia>
                </Militancias>
            </Diputado>
            <Distrito>
                <Numero>8</Numero>
                <Comunas>
                    <Comuna>
                        <Numero>13101</Numero>
                        <Nombre>Santiago</Nombre>
                    </Comuna>
                </Comunas>
            </Distrito>
        </DiputadoPeriodo>
        """
    )

    client = OpenDataCamaraClient()

    raw = client._parse_diputado_periodo(periodo)

    assert raw["id"] == 1254
    assert raw["period_start_date"] == "2026-03-11"
    assert raw["period_end_date"] is None
    assert raw["district_number"] == 8
    assert raw["district_communes"] == [{"number": 13101, "name": "Santiago"}]


def test_legislator_parser_opendata_deputy_emits_per_militancia_terms():
    payload = LegislatorParser.parse_opendata_deputy(
        {
            "id": 1254,
            "first_name": "Ignacio",
            "second_name": "",
            "last_name_father": "Urcullú",
            "last_name_mother": "Clèment-Lund",
            "birth_date": "1976-03-07",
            "gender": "Masculino",
            "gender_code": "1",
            "militancias": [
                {
                    "start_date": "2018-03-11",
                    "end_date": "2022-03-10",
                    "party_name": "Renovación Nacional",
                    "party_alias": "RN",
                },
                {
                    "start_date": "2022-03-11",
                    "end_date": None,
                    "party_name": "Partido Republicano",
                    "party_alias": "PREP",
                },
            ],
        }
    )

    assert payload is not None
    assert payload["source"] == "opendata_camara"
    assert payload["source_external_id"] == "1254"
    assert payload["full_name"] == "Ignacio Urcullú Clèment-Lund"
    assert payload["paternal_last_name"] == "Urcullú"
    assert payload["maternal_last_name"] == "Clèment-Lund"
    # Each militancia becomes a term, all sharing the camara bridge.
    assert len(payload["terms"]) == 2
    term = payload["terms"][0]
    assert term["chamber_type"].value == "deputies"
    assert term["chamber_external_id"] == "camara:1254"
    assert term["party_name"] == "Renovación Nacional"
    assert term["party_alias"] == "RN"
    assert term["party_source"] == "opendata"


def test_legislator_parser_opendata_deputy_returns_none_when_id_missing():
    assert LegislatorParser.parse_opendata_deputy({"id": None}) is None


def test_legislator_parser_opendata_deputy_skips_malformed_militancia_with_end_before_start():
    # Regression: OpenData has emitted militancias whose FechaTermino predates
    # FechaInicio (deputy 1180, Consuelo Veloso, carries
    # 2026-03-11 → 2026-03-10 alongside her real 2026-2030 row). The malformed
    # entry collided on (chamber, start_date) in _reconcile_terms and clobbered
    # the valid term's end_date, dropping her from the active-deputies count.
    payload = LegislatorParser.parse_opendata_deputy(
        {
            "id": 1180,
            "first_name": "Consuelo",
            "last_name_father": "Veloso",
            "last_name_mother": "Ávila",
            "militancias": [
                {
                    "start_date": "2022-03-11",
                    "end_date": "2024-05-30",
                    "party_name": "Revolución Democrática",
                    "party_alias": "RD",
                },
                {
                    "start_date": "2024-05-31",
                    "end_date": "2026-03-10",
                    "party_name": "Independientes",
                    "party_alias": "IND",
                },
                {
                    "start_date": "2026-03-11",
                    "end_date": "2030-03-10",
                    "party_name": "Frente Amplio",
                    "party_alias": "FA",
                },
                # Malformed — closing date precedes opening date.
                {
                    "start_date": "2026-03-11",
                    "end_date": "2026-03-10",
                    "party_name": "Independientes",
                    "party_alias": "IND",
                },
            ],
        }
    )

    assert payload is not None
    assert len(payload["terms"]) == 3
    current_term = next(
        term for term in payload["terms"] if term["start_date"] == "2026-03-11"
    )
    assert current_term["end_date"] == "2030-03-10"
    assert current_term["party_name"] == "Frente Amplio"
    assert current_term["party_alias"] == "FA"


def test_legislator_parser_senator_emits_term_per_periodo():
    payload = LegislatorParser.parse_senator(
        {
            "ID_PARLAMENTARIO": 1335,
            "NOMBRE": "Javier",
            "APELLIDO_PATERNO": "Macaya",
            "APELLIDO_MATERNO": "Danús",
            "NOMBRE_COMPLETO": "Javier Macaya Danús",
            "PARTIDO": "U.D.I.",
            "CIRCUNSCRIPCION_ID": 8,
            "REGION": "Región del Libertador General Bernardo O'Higgins",
            "SEXO": "2",
            "SEXO_ETIQUETA": "Hombre",
            "SLUG": "javier-macaya-danus-sen",
            "PERIODOS": [
                {"CAMARA": "S", "DESDE": "2026", "HASTA": "2030", "VIGENTE": 1},
                {"CAMARA": "S", "DESDE": "2022", "HASTA": "2026", "VIGENTE": 0},
                {"CAMARA": "D", "DESDE": "2018", "HASTA": "2022", "VIGENTE": 0},
            ],
        }
    )

    assert payload is not None
    assert payload["source"] == "senado_web"
    assert payload["source_external_id"] == "1335"
    assert payload["full_name"] == "Javier Macaya Danús"
    assert payload["gender"] == "M"
    assert len(payload["terms"]) == 3

    senate_current = payload["terms"][0]
    assert senate_current["chamber_external_id"] == "senado:1335"
    assert senate_current["chamber_type"].value == "senate"
    assert senate_current["start_date"] == "2026-03-11"
    # Only the active senate term picks up the top-level PARTIDO; historical
    # senate stints have no upstream party signal.
    assert senate_current["party_name"] == "U.D.I."
    assert senate_current["party_source"] == "senado_abbreviation"

    # The deputy stint embedded in a senator's history carries no chamber
    # bridge — the reconciliation against OpenData fills it in by overlap.
    deputy_past = payload["terms"][2]
    assert deputy_past["chamber_type"].value == "deputies"
    assert deputy_past["chamber_external_id"] is None
    assert deputy_past["party_name"] == ""
    assert deputy_past["party_source"] is None


def test_legislator_parser_senator_returns_none_for_empty_periodos():
    # The empty-PERIODOS stub records in hemicycle are duplicate persons
    # under a second ID_PARLAMENTARIO; treating them as separate people
    # would create exactly the cross-chamber duplicate we're preventing.
    payload = LegislatorParser.parse_senator(
        {"ID_PARLAMENTARIO": 1042, "NOMBRE": "Javier", "PERIODOS": []}
    )
    assert payload is None


def test_legislator_parser_senator_maps_female_gender():
    payload = LegislatorParser.parse_senator(
        {
            "ID_PARLAMENTARIO": 1,
            "SEXO": "1",
            "SEXO_ETIQUETA": "Mujer",
            "PERIODOS": [
                {"CAMARA": "S", "DESDE": "2026", "HASTA": "2030", "VIGENTE": 1}
            ],
        }
    )
    assert payload["gender"] == "F"


def test_legislator_parser_bcn_profile_normalizes_gender_and_collapses_empties():
    payload = LegislatorParser.parse_bcn_profile(
        {
            "personUri": "http://datos.bcn.cl/recurso/persona/4558",
            "full_name": "Álvaro Jorge Carter Fernández",
            "profession": "Diseñador Industrial",
            "twitter": "Alvaro_CarterF",
            "bcn_wiki_url": "https://www.bcn.cl/historiapolitica/resenas/Carter",
            "gender": "hombre",
            "photo_url": "https://www.bcn.cl/laborparlamentaria/imagen/4558.jpg",
            "photo_thumbnail_url": "",
        }
    )

    assert payload["bcn_uri"].endswith("/persona/4558")
    assert payload["bcn_wiki_url"].startswith("https://www.bcn.cl/historiapolitica/")
    assert payload["profession"] == "Diseñador Industrial"
    assert payload["twitter_handle"] == "Alvaro_CarterF"
    assert payload["gender"] == "M"
    assert payload["photo_url"].endswith("/4558.jpg")
    assert payload["photo_thumbnail_url"] is None


def test_legislator_parser_bcn_profile_maps_female_gender():
    payload = LegislatorParser.parse_bcn_profile({"gender": "Mujer"})
    assert payload["gender"] == "F"


def test_legislator_parser_bcn_profile_returns_none_gender_for_unknown_label():
    payload = LegislatorParser.parse_bcn_profile({"gender": "no binario"})
    assert payload["gender"] is None


def test_legislator_parser_bcn_appointment_for_senator_term():
    payload = LegislatorParser.parse_bcn_appointment(
        {
            "appointmentUri": "http://datos.bcn.cl/recurso/persona/4558/nombramiento/2",
            "cargoId": "2",
            "term_start": "2022-03-11",
            "term_end": "2030-03-11",
        }
    )

    assert payload is not None
    assert payload["bcn_appointment_uri"].endswith("/nombramiento/2")
    assert payload["chamber_type"] == ChamberType.SENATE
    assert payload["start_date"] == "2022-03-11"
    assert payload["end_date"] == "2030-03-11"


def test_legislator_parser_bcn_appointment_returns_none_when_dates_missing():
    payload = LegislatorParser.parse_bcn_appointment(
        {
            "appointmentUri": "http://x/y",
            "cargoId": "1",
            "term_start": None,
            "term_end": "2030-03-11",
        }
    )
    assert payload is None


def test_legislator_parser_bcn_appointment_returns_none_for_unknown_cargo():
    payload = LegislatorParser.parse_bcn_appointment(
        {
            "appointmentUri": "http://x/y",
            "cargoId": "9",
            "term_start": "2022-03-11",
            "term_end": "2030-03-11",
        }
    )
    assert payload is None


# --- restsil (portallegislativo) parsers -----------------------------------


def test_bill_parser_parses_restsil_summary_for_senate_message():
    payload = BillParser.parse_restsil_summary(
        {
            "PROYID": 18974,
            "PROYNUMEROBOLETIN": "18296-05",
            "REFUNDIDOS": "",
            "PROYFECHAINGRESO": "03/06/2026",
            "PROYORIGEN": "D",
            "CAMARA_ORIGEN": "C.Diputados",
            "PROYSUMA": "Autoriza mayor endeudamiento del gobierno central durante el año 2026",
            "ETAPA": "Primer trámite constitucional (C.Diputados)",
            "SUBETAPA": "Primer informe de comisión de Hacienda",
            "PROYINICIATIVA": 30,
            "PROYDESCINICIATIVA": "Mensaje",
            "PROYURGENCIA": "Suma",
            "AUTORES": "Ministerio de Hacienda",
            "ID_PROYECTO": 18974,
        }
    )

    assert payload["bulletin_number"] == "18296-05"
    assert payload["entry_date"] == "2026-06-03"
    assert payload["origin_chamber_type"] is ChamberType.DEPUTIES
    assert payload["origin_type"] is BillOrigin.EXECUTIVE
    assert payload["urgency_label"] == "Suma"
    assert payload["proy_id"] == 18974


FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_bill_parser_restsil_detail_mensaje_emits_parse_bill_contract():
    raw = _load_fixture("restsil_bill_detail_18872.json")
    payload = BillParser.parse_restsil_detail(
        raw, bulletin="18216-05", authors_text="Ministerio de Hacienda"
    )

    # Same top-level keys as parse_bill
    assert set(payload.keys()) == {
        "bulletin_number",
        "title",
        "entry_date",
        "origin_type",
        "_origin_chamber_type",
        "status",
        "law_number",
        "publication_date",
        "message_url",
        "_current_urgency_type",
        "authors",
        "stages",
        "events",
        "documents",
        "_votaciones",
    }

    assert payload["bulletin_number"] == "18216-05"
    assert payload["title"].startswith("Para la reconstrucción nacional")
    assert payload["entry_date"] == "2026-04-22"
    assert payload["origin_type"] is BillOrigin.EXECUTIVE
    assert payload["_origin_chamber_type"] is ChamberType.DEPUTIES
    assert payload["_current_urgency_type"] is UrgencyType.SUM
    assert payload["status"] is BillStatus.PENDING
    assert payload["law_number"] == ""
    assert payload["message_url"].startswith(
        "https://microservicio-documentos.senado.cl/"
    )
    assert payload["_votaciones"] == []  # dedicated vote tasks own these

    # AUTORES "Ministerio de Hacienda" is one row (mensaje carrier); the
    # canonical-key matcher in _reconcile_authorships will fail to match
    # against any Legislator and emit the standard WARNING — that's fine,
    # the mensaje case is the one we don't have authors for.
    assert payload["authors"] == [{"name": "Ministerio de Hacienda"}]

    # Etapas → stages
    assert len(payload["stages"]) == 2
    assert payload["stages"][0]["stage_type"] is StageType.FIRST_CONSTITUTIONAL_TRAMITE
    assert payload["stages"][0]["start_date"] == "2026-04-22"
    assert payload["stages"][0]["_chamber_type"] is ChamberType.DEPUTIES
    assert payload["stages"][1]["stage_type"] is StageType.SECOND_CONSTITUTIONAL_TRAMITE
    assert payload["stages"][1]["_chamber_type"] is ChamberType.SENATE

    # Events ← tramitacionProyecto (one per row with date + text)
    assert len(payload["events"]) >= 30
    assert all(event["event_date"] for event in payload["events"])
    assert all(event["title"] for event in payload["events"])

    # Documents — each tramitación row with a LINK_X emits one BillDocument
    informes = [d for d in payload["documents"] if d["document_type"] == "report"]
    comparados = [d for d in payload["documents"] if d["document_type"] == "comparison"]
    oficios = [
        d
        for d in payload["documents"]
        if d["document_type"] == "official_communication"
    ]
    assert len(informes) >= 2
    assert len(comparados) >= 1
    assert len(oficios) >= 5
    for doc in payload["documents"]:
        assert doc["document_url"].startswith(
            "https://microservicio-documentos.senado.cl/"
        )


def test_bill_parser_restsil_detail_mocion_uses_link_mensaje_for_full_text():
    raw = _load_fixture("restsil_bill_detail_19090.json")
    payload = BillParser.parse_restsil_detail(
        raw,
        bulletin="18407-25",
        authors_text="Sepúlveda Orbenes, Alejandra/ Velásquez Núñez, Esteban",
    )

    assert payload["origin_type"] is BillOrigin.DEPUTIES
    # Even for mociones, etapas[0].link_mensaje carries the moción PDF
    assert payload["message_url"].startswith(
        "https://microservicio-documentos.senado.cl/"
    )
    # Authors split on "/" and stripped — canonical-key matcher handles
    # "Apellido_paterno Apellido_materno, Nombres" form natively.
    assert payload["authors"] == [
        {"name": "Sepúlveda Orbenes, Alejandra"},
        {"name": "Velásquez Núñez, Esteban"},
    ]


def test_bill_parser_restsil_detail_status_derives_from_etapa_label():
    # Hand-rolled raw envelope — only the fields the status mapping reads.
    raw = {
        "infoProyecto": {
            "Suma": "x",
            "Iniciativa": "Mensaje",
            "Origen": "Senado",
            "Urgencia": "Sin urgencia",
            "EstadoProyecto": "Archivado",
            "leynro": None,
            "DiarioOficial": None,
        },
        "etapasProyecto": [
            {
                "etapa": "Primer trámite constitucional",
                "camDelTramite": "Senado",
                "fechaInicio": "01/01/2024",
                "sxetid": 1,
                "link_mensaje": None,
            },
        ],
        "tramitacionProyecto": [],
    }
    payload = BillParser.parse_restsil_detail(raw, bulletin="0-99")
    assert payload["status"] is BillStatus.ARCHIVED

    raw["infoProyecto"]["EstadoProyecto"] = "Tramitación terminada"
    raw["infoProyecto"]["leynro"] = "21500"
    payload = BillParser.parse_restsil_detail(raw, bulletin="0-99")
    assert (
        payload["status"] is BillStatus.APPROVED
    )  # leynro doesn't override terminal mapping
    assert payload["law_number"] == "21500"

    raw["infoProyecto"]["EstadoProyecto"] = "Primer trámite constitucional"
    raw["infoProyecto"]["leynro"] = "21500"
    payload = BillParser.parse_restsil_detail(raw, bulletin="0-99")
    # leynro set + non-terminal etapa → PUBLISHED fallback
    assert payload["status"] is BillStatus.PUBLISHED


def test_bill_parser_restsil_detail_authors_text_handles_empty_and_whitespace():
    raw = {
        "infoProyecto": {
            "Suma": "x",
            "Iniciativa": "Moción",
            "Origen": "Senado",
            "Urgencia": "",
            "EstadoProyecto": "Primer trámite constitucional",
            "leynro": None,
            "DiarioOficial": None,
        },
        "etapasProyecto": [],
        "tramitacionProyecto": [],
    }
    payload = BillParser.parse_restsil_detail(raw, bulletin="0-99", authors_text=None)
    assert payload["authors"] == []

    payload = BillParser.parse_restsil_detail(
        raw, bulletin="0-99", authors_text="  /  /Doe, John/ "
    )
    assert payload["authors"] == [{"name": "Doe, John"}]


def test_bill_parser_restsil_summary_handles_unknown_codes_gracefully():
    payload = BillParser.parse_restsil_summary(
        {
            "PROYNUMEROBOLETIN": " 18300-04 ",
            "PROYFECHAINGRESO": "",
            "PROYORIGEN": "X",
            "PROYINICIATIVA": 99,
            "PROYSUMA": None,
        }
    )

    assert payload["bulletin_number"] == "18300-04"
    assert payload["entry_date"] is None
    assert payload["origin_chamber_type"] is None
    assert payload["origin_type"] is None
    assert payload["summary_title"] == ""


def test_vote_parser_restsil_senate_vote_uses_id_votacion_as_dedup_key():
    payload = VoteParser.parse_restsil_senate_vote(
        {
            "ID_VOTACION": 11110,
            "ID_SESION": 10191,
            "NUMERO_SESION": 26,
            "DESCRIPCION_SESION": "(03/06/2026) Sesion 26 de legislatura 374",
            "FECHA_VOTACION": "03-06-2026 18:45:02",
            "HORA": "03/06/2026 18:42",
            "TEMA": (
                "Proyecto de ley, en segundo trámite constitucional, que deroga "
                "la ley N° 18.356 (discusión en general). (Boletines Nos "
                "15.767-29 y 16.248-29, refundidos)"
            ),
            "QUORUM": "Mayoría simple",
            "BOLETIN": "15767-29",
            "SI": 31,
            "NO": 0,
            "ABS": 0,
            "PAREO": 9,
            "VOTACIONES": {
                "SI": [
                    {
                        "UUID": "049F997F-DA9A-6F29-E063-5968A8C00BC5",
                        "PARLID": 911,
                        "APELLIDO_PATERNO": "Kuschel",
                        "APELLIDO_MATERNO": "Silva",
                        "NOMBRE": "Carlos Ignacio",
                        "SLUG": "carlos-ignacio-kuschel-silva-sen",
                    }
                ],
                "NO": 0,
                "ABSTENCION": 0,
                "PAREO": [
                    {
                        "UUID": "049F997F-DCA5-6F29-E063-5968A8C00BC5",
                        "PARLID": 1342,
                        "APELLIDO_PATERNO": "Van Rysselberghe",
                        "APELLIDO_MATERNO": "Herrera",
                        "NOMBRE": "Enrique",
                        "SLUG": "enrique-van-rysselberghe-herrera-sen",
                    }
                ],
            },
        }
    )

    assert payload["bcn_id"] == "senado:vot:11110"
    assert payload["_chamber_type"] is ChamberType.SENATE
    assert payload["bill_bulletin"] == "15767-29"
    assert payload["session_ref"] == "26"
    assert payload["voting_type"] is VotingType.GENERAL  # inferred from TEMA
    assert payload["stage_label"] is None
    assert payload["result"] is VotingResult.APPROVED
    assert payload["votes_for"] == 31
    assert payload["paired_count"] == 9
    assert payload["voting_date"] == "2026-06-03T18:45:02"

    individual = payload["individual_votes"]
    assert {v["legislator_external_id"] for v in individual} == {
        "senado:911",
        "senado:1342",
    }
    assert (
        next(v for v in individual if v["legislator_external_id"] == "senado:911")[
            "vote"
        ]
        is VoteChoice.FOR
    )
    assert (
        next(v for v in individual if v["legislator_external_id"] == "senado:1342")[
            "vote"
        ]
        is VoteChoice.PAIRED
    )
    assert individual[0]["_legislator_name"] == "Carlos Ignacio Kuschel Silva"


def test_vote_parser_restsil_falls_back_to_hora_when_fecha_votacion_missing():
    # Old historical votes (e.g. ID_VOTACION 5532, from 2014) return
    # FECHA_VOTACION=None; the upstream date lives only in HORA, which is
    # minute-precision DD/MM/YYYY HH:MM. Without the fallback ``_parse_datetime``
    # falls through to the now() sentinel and rows look like fresh activity.
    payload = VoteParser.parse_restsil_senate_vote(
        {
            "ID_VOTACION": 5532,
            "FECHA_VOTACION": None,
            "HORA": "07/10/2014 18:24",
            "BOLETIN": "7011-07",
            "TEMA": "Indicación renovada al proyecto de ley",
            "SI": 23,
            "NO": 0,
            "ABS": 0,
            "PAREO": 0,
            "VOTACIONES": {"SI": 0, "NO": 0, "ABSTENCION": 0, "PAREO": 0},
        }
    )

    # HORA is minute-precision; seconds zero-padded.
    assert payload["voting_date"] == "2014-10-07T18:24:00"


def test_vote_parser_restsil_handles_empty_buckets_emitted_as_integers():
    payload = VoteParser.parse_restsil_senate_vote(
        {
            "ID_VOTACION": 1,
            "TEMA": "La supresión del número 5 del artículo 15",
            "BOLETIN": "15975-25",
            "SI": 0,
            "NO": 0,
            "ABS": 0,
            "PAREO": 0,
            "VOTACIONES": {"SI": 0, "NO": 0, "ABSTENCION": 0, "PAREO": 0},
            "FECHA_VOTACION": "03-06-2026 18:32:16",
            "QUORUM": "",
        }
    )

    assert payload["bcn_id"] == "senado:vot:1"
    assert payload["result"] is None
    assert payload["voting_type"] is VotingType.OTHER  # no TEMA hint
    assert payload["individual_votes"] == []


def test_legislator_parser_bcn_rest_enrichment_keyed_by_chamber_bridge():
    enrichment = LegislatorParser.parse_bcn_rest_enrichment(
        {
            "bcn_uri": "http://datos.bcn.cl/recurso/persona/2717",
            "id_wiki": "Alejandra_Sepúlveda_Orbenes",
            "id_en_camara_de_origen": 1341,
            "camara_id": 261,
        }
    )
    assert enrichment is not None
    assert enrichment["chamber_external_id"] == "senado:1341"
    assert enrichment["bcn_uri"] == "http://datos.bcn.cl/recurso/persona/2717"
    assert enrichment["bcn_wiki_url"] == (
        "https://www.bcn.cl/historiapolitica/resenas_parlamentarias/wiki"
        "/Alejandra_Sepúlveda_Orbenes"
    )


def test_legislator_parser_bcn_rest_enrichment_returns_none_without_bridge():
    # No bridge = nothing to key the enrichment off — the new resolver no
    # longer has Legislator.bcn_id to attach to.
    enrichment = LegislatorParser.parse_bcn_rest_enrichment(
        {"bcn_uri": "http://x", "id_wiki": "y"}
    )
    assert enrichment is None


def test_legislator_parser_bcn_rest_enrichment_returns_none_when_all_fields_empty():
    enrichment = LegislatorParser.parse_bcn_rest_enrichment(
        {
            "bcn_uri": "",
            "id_wiki": "",
            "id_en_camara_de_origen": 1341,
            "camara_id": 261,
        }
    )
    assert enrichment is None


def test_legislature_parser_emits_ordinaria_kind_for_annual_cycle():
    parsed = LegislatureParser.parse_legislature(
        {
            "id": "374",
            "number": 374,
            "type": "Ordinaria",
            "start_date": "2026-03-11",
            "end_date": "2027-03-11",
        }
    )
    assert parsed["number"] == 374
    assert parsed["kind"] == "ordinaria"
    assert parsed["start_date"] == "2026-03-11"
    assert parsed["end_date"] == "2027-03-11"


def test_legislature_parser_maps_extraordinaria_for_pre_2005_records():
    parsed = LegislatureParser.parse_legislature(
        {"id": "100", "type": "Extraordinaria"}
    )
    assert parsed["kind"] == "extraordinaria"


def test_session_parser_maps_especial_at_meeting_level_not_extraordinaria():
    # At the session (meeting) level the subtype is ordinaria/especial — see
    # CONTEXT.md "Sesión Legislativa". Upstream payloads that still say
    # "Extraordinaria" are normalized down to especial.
    ordinaria = LegislatureParser.parse_session(
        {"id": "1", "number": 12, "type": "Ordinaria", "date": "2026-04-08"},
        legislature_number=374,
    )
    especial = LegislatureParser.parse_session(
        {"id": "2", "number": 13, "type": "Especial", "date": "2026-04-09"},
        legislature_number=374,
    )
    extraordinaria_normalized = LegislatureParser.parse_session(
        {"id": "3", "number": 14, "type": "Extraordinaria", "date": "2026-04-10"},
        legislature_number=374,
    )
    assert ordinaria["kind"] == "ordinaria"
    assert especial["kind"] == "especial"
    assert extraordinaria_normalized["kind"] == "especial"
    assert ordinaria["_legislature_number"] == 374


def test_legislative_period_parser_handles_non_integer_id():
    parsed = LegislatureParser.parse_legislative_period(
        {"id": "no_un_numero", "name": "X", "start_date": "2018-03-11"}
    )
    # except-tuple syntax should swallow the ValueError, leaving number=None.
    assert parsed["number"] is None
    assert parsed["description"] == "X"
