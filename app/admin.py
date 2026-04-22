"""sqladmin admin panel — mounts at /admin."""

from sqladmin import Admin, ModelView
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from app.core.config import settings
from app.core.database import engine
from app.models.core import Circumscription, District, Region, Topic
from app.models.diario_oficial import NormaGeneral, Reglamento, ReglamentoEtapa
from app.models.legislature import (
    Chamber,
    Coalition,
    Committee,
    LegislativePeriod,
    LegislativeSession,
    Legislator,
    PoliticalParty,
)
from app.models.proyecto import Bill, BillEvent, BillStage, BillUrgency
from app.models.votacion import LegislatorVotingStats, Vote, VotingSession


# ── Authentication ────────────────────────────────────────────────────

class AdminAuth(AuthenticationBackend):
    async def login(self, request: Request) -> bool:
        form = await request.form()
        if (
            form.get("username") == settings.admin_username
            and form.get("password") == settings.admin_password
        ):
            request.session.update({"admin": True})
            return True
        return False

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        return request.session.get("admin", False)


# ── Diario Oficial ────────────────────────────────────────────────────

class NormaGeneralAdmin(ModelView, model=NormaGeneral):
    name = "Norma"
    name_plural = "Normas Generales"
    icon = "fa-solid fa-newspaper"
    category = "Diario Oficial"

    column_list = [
        NormaGeneral.id,
        NormaGeneral.date,
        NormaGeneral.branch,
        NormaGeneral.ministry,
        NormaGeneral.title,
        NormaGeneral.cve,
        NormaGeneral.categoria_ia,
        NormaGeneral.importancia_ciudadana,
        NormaGeneral.sync_version,
    ]
    column_details_list = "__all__"
    column_searchable_list = [NormaGeneral.title, NormaGeneral.cve, NormaGeneral.ministry, NormaGeneral.organ]
    column_sortable_list = [NormaGeneral.id, NormaGeneral.date, NormaGeneral.importancia_ciudadana, NormaGeneral.sync_version]
    page_size = 50
    can_delete = False


class ReglamentoAdmin(ModelView, model=Reglamento):
    name = "Reglamento"
    name_plural = "Reglamentos"
    icon = "fa-solid fa-file-contract"
    category = "Diario Oficial"

    column_list = [
        Reglamento.id,
        Reglamento.numero,
        Reglamento.anio,
        Reglamento.ministerio,
        Reglamento.categoria,
        Reglamento.estado,
        Reglamento.fecha_ingreso,
        Reglamento.reingresado,
        Reglamento.sync_version,
    ]
    column_details_list = "__all__"
    column_searchable_list = [Reglamento.numero, Reglamento.ministerio, Reglamento.materia, Reglamento.categoria]
    column_sortable_list = [Reglamento.id, Reglamento.anio, Reglamento.ministerio, Reglamento.fecha_ingreso, Reglamento.sync_version]
    page_size = 50
    can_delete = False


class ReglamentoEtapaAdmin(ModelView, model=ReglamentoEtapa):
    name = "Etapa"
    name_plural = "Etapas de Reglamentos"
    icon = "fa-solid fa-list-check"
    category = "Diario Oficial"

    column_list = [
        ReglamentoEtapa.id,
        ReglamentoEtapa.reglamento_id,
        ReglamentoEtapa.etapa,
        ReglamentoEtapa.fecha,
        ReglamentoEtapa.accion,
        ReglamentoEtapa.sector,
    ]
    column_sortable_list = [ReglamentoEtapa.id, ReglamentoEtapa.fecha]
    page_size = 100
    can_delete = False


# ── Parlamento ────────────────────────────────────────────────────────

class LegislatorAdmin(ModelView, model=Legislator):
    name = "Legislador"
    name_plural = "Legisladores"
    icon = "fa-solid fa-user-tie"
    category = "Parlamento"

    column_list = [
        Legislator.id,
        Legislator.full_name,
        Legislator.chamber_type,
        Legislator.party,
        Legislator.district,
        Legislator.circumscription,
        Legislator.is_active,
    ]
    column_details_list = "__all__"
    column_searchable_list = [Legislator.full_name, Legislator.first_name, Legislator.last_name, Legislator.email, Legislator.bcn_id]
    column_sortable_list = [Legislator.id, Legislator.last_name, Legislator.chamber_type, Legislator.is_active]
    page_size = 50


class PoliticalPartyAdmin(ModelView, model=PoliticalParty):
    name = "Partido"
    name_plural = "Partidos Políticos"
    icon = "fa-solid fa-flag"
    category = "Parlamento"

    column_list = [
        PoliticalParty.id,
        PoliticalParty.name,
        PoliticalParty.abbreviation,
        PoliticalParty.is_active,
        PoliticalParty.founded_date,
        PoliticalParty.color,
    ]
    column_searchable_list = [PoliticalParty.name, PoliticalParty.abbreviation]
    column_sortable_list = [PoliticalParty.id, PoliticalParty.name, PoliticalParty.is_active]


class CoalitionAdmin(ModelView, model=Coalition):
    name = "Coalición"
    name_plural = "Coaliciones"
    icon = "fa-solid fa-people-group"
    category = "Parlamento"

    column_list = [Coalition.id, Coalition.name, Coalition.abbreviation, Coalition.is_active]
    column_searchable_list = [Coalition.name, Coalition.abbreviation]


class ChamberAdmin(ModelView, model=Chamber):
    name = "Cámara"
    name_plural = "Cámaras"
    icon = "fa-solid fa-building-columns"
    category = "Parlamento"

    column_list = [Chamber.id, Chamber.chamber_type, Chamber.name, Chamber.total_seats]


class LegislativePeriodAdmin(ModelView, model=LegislativePeriod):
    name = "Período"
    name_plural = "Períodos Legislativos"
    icon = "fa-solid fa-calendar-days"
    category = "Parlamento"

    column_list = [LegislativePeriod.id, LegislativePeriod.number, LegislativePeriod.start_date, LegislativePeriod.end_date, LegislativePeriod.description]
    column_sortable_list = [LegislativePeriod.number, LegislativePeriod.start_date]


class LegislativeSessionAdmin(ModelView, model=LegislativeSession):
    name = "Sesión"
    name_plural = "Sesiones Legislativas"
    icon = "fa-solid fa-gavel"
    category = "Parlamento"

    column_list = [LegislativeSession.id, LegislativeSession.number, LegislativeSession.session_type, LegislativeSession.chamber, LegislativeSession.period, LegislativeSession.start_date, LegislativeSession.end_date]
    column_sortable_list = [LegislativeSession.id, LegislativeSession.start_date, LegislativeSession.number]
    page_size = 50


class CommitteeAdmin(ModelView, model=Committee):
    name = "Comisión"
    name_plural = "Comisiones"
    icon = "fa-solid fa-users"
    category = "Parlamento"

    column_list = [Committee.id, Committee.name, Committee.chamber, Committee.committee_type, Committee.is_active]
    column_searchable_list = [Committee.name]
    column_sortable_list = [Committee.id, Committee.name, Committee.is_active]


# ── Legislación ───────────────────────────────────────────────────────

class TopicAdmin(ModelView, model=Topic):
    name = "Tema"
    name_plural = "Temas"
    icon = "fa-solid fa-tags"
    category = "Legislación"

    column_list = [Topic.id, Topic.name, Topic.slug, Topic.parent]
    column_searchable_list = [Topic.name, Topic.slug]
    column_sortable_list = [Topic.id, Topic.name]


class BillAdmin(ModelView, model=Bill):
    name = "Proyecto"
    name_plural = "Proyectos de Ley"
    icon = "fa-solid fa-scale-balanced"
    category = "Legislación"

    column_list = [
        Bill.id,
        Bill.bulletin_number,
        Bill.title,
        Bill.bill_type,
        Bill.status,
        Bill.entry_date,
        Bill.origin_chamber,
        Bill.current_chamber,
        Bill.law_number,
        Bill.sync_version,
    ]
    column_details_list = "__all__"
    column_searchable_list = [Bill.title, Bill.bulletin_number, Bill.bcn_id, Bill.law_number]
    column_sortable_list = [Bill.id, Bill.entry_date, Bill.status, Bill.bill_type, Bill.sync_version]
    page_size = 50
    can_delete = False


class BillStageAdmin(ModelView, model=BillStage):
    name = "Trámite"
    name_plural = "Trámites"
    icon = "fa-solid fa-arrows-turn-right"
    category = "Legislación"

    column_list = [
        BillStage.id,
        BillStage.bill,
        BillStage.stage_type,
        BillStage.chamber,
        BillStage.start_date,
        BillStage.end_date,
        BillStage.result,
        BillStage.is_current,
    ]
    column_sortable_list = [BillStage.id, BillStage.start_date]
    page_size = 100
    can_delete = False


class BillEventAdmin(ModelView, model=BillEvent):
    name = "Evento"
    name_plural = "Eventos de Proyectos"
    icon = "fa-solid fa-clock-rotate-left"
    category = "Legislación"

    column_list = [BillEvent.id, BillEvent.bill, BillEvent.chamber, BillEvent.event_date, BillEvent.title]
    column_searchable_list = [BillEvent.title]
    column_sortable_list = [BillEvent.id, BillEvent.event_date]
    page_size = 100
    can_delete = False


class BillUrgencyAdmin(ModelView, model=BillUrgency):
    name = "Urgencia"
    name_plural = "Urgencias"
    icon = "fa-solid fa-triangle-exclamation"
    category = "Legislación"

    column_list = [BillUrgency.id, BillUrgency.bill, BillUrgency.urgency_type, BillUrgency.chamber, BillUrgency.entry_date, BillUrgency.deadline_date, BillUrgency.is_active]
    column_sortable_list = [BillUrgency.id, BillUrgency.entry_date, BillUrgency.deadline_date]
    can_delete = False


# ── Votaciones ────────────────────────────────────────────────────────

class VotingSessionAdmin(ModelView, model=VotingSession):
    name = "Votación"
    name_plural = "Votaciones"
    icon = "fa-solid fa-check-to-slot"
    category = "Votaciones"

    column_list = [
        VotingSession.id,
        VotingSession.chamber,
        VotingSession.voting_date,
        VotingSession.result,
        VotingSession.votes_for,
        VotingSession.votes_against,
        VotingSession.abstentions,
        VotingSession.absences,
        VotingSession.subject,
    ]
    column_searchable_list = [VotingSession.subject, VotingSession.bcn_id]
    column_sortable_list = [VotingSession.id, VotingSession.voting_date, VotingSession.result]
    page_size = 50
    can_delete = False


class VoteAdmin(ModelView, model=Vote):
    name = "Voto"
    name_plural = "Votos"
    icon = "fa-solid fa-hand"
    category = "Votaciones"

    column_list = [Vote.id, Vote.voting_session, Vote.legislator, Vote.vote]
    column_sortable_list = [Vote.id, Vote.vote]
    page_size = 100
    can_delete = False


class LegislatorVotingStatsAdmin(ModelView, model=LegislatorVotingStats):
    name = "Estadísticas"
    name_plural = "Estadísticas de Votación"
    icon = "fa-solid fa-chart-bar"
    category = "Votaciones"

    column_list = [
        LegislatorVotingStats.id,
        LegislatorVotingStats.legislator,
        LegislatorVotingStats.total_sessions,
        LegislatorVotingStats.votes_for,
        LegislatorVotingStats.votes_against,
    ]
    column_sortable_list = [LegislatorVotingStats.id, LegislatorVotingStats.total_sessions, LegislatorVotingStats.votes_for]
    can_delete = False


# ── Geografía ─────────────────────────────────────────────────────────

class RegionAdmin(ModelView, model=Region):
    name = "Región"
    name_plural = "Regiones"
    icon = "fa-solid fa-map"
    category = "Geografía"

    column_list = [Region.id, Region.number, Region.name, Region.capital]
    column_sortable_list = [Region.number, Region.name]


class DistrictAdmin(ModelView, model=District):
    name = "Distrito"
    name_plural = "Distritos"
    icon = "fa-solid fa-map-pin"
    category = "Geografía"

    column_list = [District.id, District.number, District.name, District.region]
    column_sortable_list = [District.number, District.name]


class CircumscriptionAdmin(ModelView, model=Circumscription):
    name = "Circunscripción"
    name_plural = "Circunscripciones"
    icon = "fa-solid fa-location-dot"
    category = "Geografía"

    column_list = [Circumscription.id, Circumscription.number, Circumscription.name]
    column_sortable_list = [Circumscription.number, Circumscription.name]


# ── Setup ─────────────────────────────────────────────────────────────

_ALL_VIEWS = [
    # Diario Oficial
    NormaGeneralAdmin,
    ReglamentoAdmin,
    ReglamentoEtapaAdmin,
    # Parlamento
    LegislatorAdmin,
    PoliticalPartyAdmin,
    CoalitionAdmin,
    ChamberAdmin,
    LegislativePeriodAdmin,
    LegislativeSessionAdmin,
    CommitteeAdmin,
    # Legislación
    TopicAdmin,
    BillAdmin,
    BillStageAdmin,
    BillEventAdmin,
    BillUrgencyAdmin,
    # Votaciones
    VotingSessionAdmin,
    VoteAdmin,
    LegislatorVotingStatsAdmin,
    # Geografía
    RegionAdmin,
    DistrictAdmin,
    CircumscriptionAdmin,
]


def setup_admin(app) -> Admin:
    auth = AdminAuth(secret_key=settings.admin_secret_key)
    admin = Admin(
        app,
        engine=engine,
        authentication_backend=auth,
        title="Cámara Abierta — Admin",
    )
    for view in _ALL_VIEWS:
        admin.add_view(view)
    return admin
