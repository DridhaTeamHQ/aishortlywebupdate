from redis import Redis
from rq import Queue

from .config import get_settings


def get_queue() -> Queue:
    settings = get_settings()
    conn = Redis.from_url(settings.redis_url)
    return Queue(settings.rq_queue_name, connection=conn)
