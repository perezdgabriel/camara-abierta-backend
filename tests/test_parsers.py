from xml.etree import ElementTree as ET

from app.ingestors.clients.senado import SenadoClient
from app.ingestors.parsers.bills import BillParser
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


def test_vote_parser_maps_votes_and_result_to_canonical_enums():
    payload = VoteParser.parse_senate_vote(
        {
            "voting_type": "Discusión general",
            "session": "42",
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
    assert payload["individual_votes"][0]["vote"] is VoteChoice.FOR
    assert payload["individual_votes"][1]["vote"] is VoteChoice.ABSENT


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
        assert bill_payload["stages"][0]["stage_type"] is StageType.FIRST_CONSTITUTIONAL_TRAMITE
        assert bill_payload["stages"][0]["_chamber_type"] is ChamberType.SENATE
        assert len(bill_payload["documents"]) == 3

        vote_payload = VoteParser.parse_senate_vote(
                bill_payload["_votaciones"][0],
                bulletin=bill_payload["bulletin_number"],
        )

        assert vote_payload["bcn_id"] == "senado:vot:555-06:42"
        assert vote_payload["voting_type"] is VotingType.GENERAL
        assert vote_payload["result"] is VotingResult.APPROVED
        assert vote_payload["individual_votes"][0]["vote"] is VoteChoice.FOR
