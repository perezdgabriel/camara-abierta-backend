"""Elasticsearch index management and search for bills (proyectos de ley)."""

import logging
from typing import Any

from app.models.proyecto import Bill
from app.search.client import get_es_client

logger = logging.getLogger(__name__)

INDEX_NAME = "bills_v1"

# ── Index mapping ─────────────────────────────────────────────────────────────

MAPPING: dict[str, Any] = {
    "settings": {
        "analysis": {
            "filter": {
                "spanish_stop": {
                    "type": "stop",
                    "stopwords": "_spanish_",
                },
                "spanish_stemmer": {
                    "type": "stemmer",
                    "language": "light_spanish",
                },
            },
            "analyzer": {
                "spanish_custom": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "spanish_stop", "spanish_stemmer"],
                }
            },
        },
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 1,
        },
    },
    "mappings": {
        "properties": {
            # Full-text search fields
            "title": {
                "type": "text",
                "analyzer": "spanish_custom",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "bulletin_number": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword"}},
            },
            "summary": {
                "type": "text",
                "analyzer": "spanish_custom",
            },
            "full_text": {
                "type": "text",
                "analyzer": "spanish_custom",
            },
            "author_names": {
                "type": "text",
                "analyzer": "spanish_custom",
            },
            # Keyword filter fields
            "bill_type": {"type": "keyword"},
            "origin": {"type": "keyword"},
            "status": {"type": "keyword"},
            "law_number": {"type": "keyword"},
            "current_stage_type": {"type": "keyword"},
            "active_urgency_type": {"type": "keyword"},
            "topic_ids": {"type": "integer"},
            "topic_slugs": {"type": "keyword"},
            # Dates
            "entry_date": {"type": "date"},
            # Metadata
            "id": {"type": "long"},
            "sync_version": {"type": "long"},
        }
    },
}


# ── Index management ──────────────────────────────────────────────────────────

def ensure_index() -> None:
    """Create the bills index with mapping if it does not exist."""
    es = get_es_client()
    if not es.indices.exists(index=INDEX_NAME):
        es.indices.create(index=INDEX_NAME, body=MAPPING)
        logger.info("Created Elasticsearch index %s", INDEX_NAME)
    else:
        logger.debug("Elasticsearch index %s already exists", INDEX_NAME)


# ── Document builder ──────────────────────────────────────────────────────────

def build_document(bill: Bill) -> dict[str, Any]:
    """Build an ES document from a fully-loaded Bill ORM object."""
    active_urgency = next((u for u in (bill.urgencies or []) if u.is_active), None)
    current_stage = next((s for s in (bill.stages or []) if s.is_current), None)
    author_names = [a.legislator.full_name for a in (bill.authorships or []) if a.legislator]

    return {
        "id": bill.id,
        "bulletin_number": bill.bulletin_number,
        "title": bill.title,
        "summary": bill.summary,
        "full_text": bill.full_text,
        "author_names": author_names,
        "bill_type": bill.bill_type,
        "origin": bill.origin,
        "status": bill.status,
        "law_number": bill.law_number,
        "entry_date": bill.entry_date.isoformat() if bill.entry_date else None,
        "current_stage_type": current_stage.stage_type if current_stage else None,
        "active_urgency_type": active_urgency.urgency_type if active_urgency else None,
        "topic_ids": [t.id for t in (bill.topics or [])],
        "topic_slugs": [t.slug for t in (bill.topics or [])],
        "sync_version": bill.sync_version,
    }


# ── Indexing ──────────────────────────────────────────────────────────────────

def index_bill(bill: Bill) -> None:
    """Index or update a single bill document."""
    es = get_es_client()
    doc = build_document(bill)
    es.index(index=INDEX_NAME, id=str(bill.id), document=doc)


def delete_bill(bill_id: int) -> None:
    """Remove a bill document from the index."""
    es = get_es_client()
    es.delete(index=INDEX_NAME, id=str(bill_id), ignore=[404])


# ── Search ────────────────────────────────────────────────────────────────────

def search_bills(
    *,
    q: str | None,
    status: str | None = None,
    bill_type: str | None = None,
    origin: str | None = None,
    topic_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[int, list[int]]:
    """
    Full-text search over bills.

    Returns (total_hits, [bill_id, ...]) — caller hydrates from DB.
    """
    es = get_es_client()

    must: list[dict] = []
    filter_clauses: list[dict] = []

    if q:
        must.append({
            "multi_match": {
                "query": q,
                "fields": [
                    "title^3",
                    "bulletin_number^2",
                    "summary",
                    "full_text",
                    "author_names",
                ],
                "type": "best_fields",
                "fuzziness": "AUTO",
                "minimum_should_match": "75%",
            }
        })

    if status:
        filter_clauses.append({"term": {"status": status}})
    if bill_type:
        filter_clauses.append({"term": {"bill_type": bill_type}})
    if origin:
        filter_clauses.append({"term": {"origin": origin}})
    if topic_id:
        filter_clauses.append({"term": {"topic_ids": topic_id}})

    date_range: dict[str, str] = {}
    if date_from:
        date_range["gte"] = date_from
    if date_to:
        date_range["lte"] = date_to
    if date_range:
        filter_clauses.append({"range": {"entry_date": date_range}})

    body: dict[str, Any] = {
        "query": {
            "bool": {
                "must": must or [{"match_all": {}}],
                "filter": filter_clauses,
            }
        },
        "_source": ["id"],
        "from": offset,
        "size": limit,
        "sort": [{"_score": "desc"}, {"entry_date": "desc"}],
    }

    resp = es.search(index=INDEX_NAME, body=body)
    total = resp["hits"]["total"]["value"]
    ids = [int(hit["_source"]["id"]) for hit in resp["hits"]["hits"]]
    return total, ids
