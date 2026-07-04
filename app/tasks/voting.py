from app.core.celery_app import app
from app.core.dispatch import dispatch
from app.core.session import task_session
from app.services import legislator_stats, voting_signals
from app.services.write import upsert_voting_session
from app.tasks.base import DatabaseTask


@app.task(name="app.tasks.voting.sync_voting_session", bind=True, base=DatabaseTask)
def sync_voting_session(self, data: dict, bill_bulletin: str | None = None) -> dict:
    with task_session() as db:
        voting_session = upsert_voting_session(db, data, bill_bulletin=bill_bulletin)
        session_id = voting_session.id
    # Dispatch signal recomputation in its own task. Decoupling lets the sync
    # finish quickly even if signal compute is heavy (e.g. PR-2 cohesion JOIN).
    dispatch(compute_voting_session_signals, session_id)
    return {"voting_session_id": session_id, "status": "ok"}


@app.task(
    name="app.tasks.voting.compute_voting_session_signals",
    bind=True,
    base=DatabaseTask,
)
def compute_voting_session_signals(self, voting_session_id: int) -> dict:
    """Compute and persist signals for one voting session.

    Idempotent: replaces any prior rows. Safe to retry; safe to re-dispatch
    after threshold changes.
    """
    with task_session() as db:
        fired = voting_signals.recompute_session_signals(db, voting_session_id)
    return {"voting_session_id": voting_session_id, "signals_fired": fired}


@app.task(
    name="app.tasks.voting.refresh_voting_window_aggregate",
    bind=True,
    base=DatabaseTask,
)
def refresh_voting_window_aggregate(self, window_days: int = 30) -> dict:
    """Recompute the rolling-window aggregates that feed the stats band on
    /votaciones. Wired to a daily beat schedule in celery_beat.py.
    """
    with task_session() as db:
        row = voting_signals.refresh_window_aggregate(db, window_days=window_days)
        payload = dict(row.payload)
    return {"window_days": window_days, "payload": payload}


@app.task(
    name="app.tasks.voting.refresh_legislator_voting_stats",
    bind=True,
    base=DatabaseTask,
)
def refresh_legislator_voting_stats(self) -> dict:
    """Recompute per-legislator voting stats: base aggregates plus inclinación de
    voto and disciplina partidaria. Feeds the simulator seed (/legisladores) and
    the legislator detail page. Wired to a daily beat schedule in celery_beat.py.
    """
    with task_session() as db:
        updated = legislator_stats.refresh_legislator_voting_stats(db)
    return {"legislators_updated": updated}
