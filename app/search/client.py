from functools import lru_cache

from elasticsearch import Elasticsearch

from app.core.config import settings


@lru_cache(maxsize=1)
def get_es_client() -> Elasticsearch:
    return Elasticsearch(settings.elasticsearch_url, request_timeout=30)
