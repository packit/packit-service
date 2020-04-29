from redis import Redis
from os import getenv


def get_db(db: int):
    return Redis(
        host=getenv("REDIS_SERVICE_HOST", "localhost"),
        port=int(getenv("REDIS_SERVICE_PORT", "6379")),
        db=db,
        decode_responses=True,
    )


if __name__ == "__main__":
    current_db = get_db(0)
    keys = current_db.keys("celery-task-meta-*")
    for key in keys:
        current_db.delete(key)

    current_db = get_db(1)
    current_db.flushdb()
