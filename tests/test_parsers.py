from xml.etree import ElementTree as ET

from app.ingestors.clients.opendata_camara import OpenDataCamaraClient
from app.ingestors.clients.senado import SenadoClient
from app.ingestors.parsers.bills import BillParser
from app.ingestors.parsers.legislators import LegislatorParser
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
            "materias": [" Transparencia ", ""],
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
    assert payload["topics"] == ["Transparencia"]
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
    assert payload["individual_votes"][1]["vote"] is VoteChoice.ABSENT


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
    assert bill_payload["topics"] == ["Transparencia"]
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


def test_legislator_parser_maps_opendata_deputy_party_and_district_number():
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
            "district_number": 8,
            "militancias": [
                {
                    "start_date": "2026-03-11",
                    "end_date": "2030-03-10",
                    "party_name": "Partido Republicano",
                    "party_alias": "PREP",
                }
            ],
        }
    )

    assert payload["bcn_id"] == "camara:1254"
    assert payload["chamber_type"] is ChamberType.DEPUTIES
    assert payload["full_name"] == "Ignacio Urcullú Clèment-Lund"
    assert payload["_party_name"] == "Partido Republicano"
    assert payload["_district_number"] == 8


def test_legislator_parser_maps_senator_from_web_json():
    payload = LegislatorParser.parse_senator(
        {
            "ID_PARLAMENTARIO": 1110,
            "NOMBRE": "Pedro",
            "APELLIDO_PATERNO": "Araya",
            "APELLIDO_MATERNO": "Guerrero",
            "NOMBRE_COMPLETO": "Pedro Araya Guerrero",
            "PARTIDO": "P.P.D.",
            "CIRCUNSCRIPCION_ID": 3,
            "REGION": "Región de Antofagasta",
            "EMAIL": "paraya@senado.cl",
            "FONO": "(56-32) 2504703",
            "SEXO": "2",
            "SEXO_ETIQUETA": "Hombre",
            "SLUG": "pedro-araya-guerrero-sen",
            "IMAGEN_450": "https://cdn.senado.cl/x_450x750.jpg",
            "IMAGEN_120": "https://cdn.senado.cl/x_120x120.jpg",
        }
    )

    assert payload["bcn_id"] == "senado:1110"
    assert payload["chamber_type"] is ChamberType.SENATE
    assert payload["full_name"] == "Pedro Araya Guerrero"
    assert payload["_party_name"] == "P.P.D."
    assert payload["_circumscription_number"] == 3
    assert payload["_region_name"] == "Región de Antofagasta"
    assert payload["gender"] == "M"
    assert payload["email"] == "paraya@senado.cl"
    assert payload["photo_url"] == "https://cdn.senado.cl/x_450x750.jpg"
    assert payload["photo_thumbnail_url"] == "https://cdn.senado.cl/x_120x120.jpg"
    assert payload["profile_url"].endswith("/pedro-araya-guerrero-sen")


def test_legislator_parser_maps_female_senator_gender():
    payload = LegislatorParser.parse_senator(
        {"ID_PARLAMENTARIO": 1, "SEXO": "1", "SEXO_ETIQUETA": "Mujer"}
    )
    assert payload["gender"] == "F"
