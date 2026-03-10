import os

from redis import Redis
from rq import Connection, Worker


if __name__ == "__main__":
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    queue_name = os.getenv("RQ_QUEUE_NAME", "agent-runs")

    conn = Redis.from_url(redis_url)
    with Connection(conn):
        worker = Worker([queue_name])
        worker.work(with_scheduler=True)
