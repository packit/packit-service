from os import getenv
from redis import Redis
import json


def clean_up_empty_tasks(db):
    keys = db.keys("celery-tasks-meta-*")

    for key in keys:
        value = json.loads(db.get(key))
        if "result" in value.keys() and value["result"] is None:
            db.delete(key)


if __name__ == "__main__":
    db = Redis(
        host=getenv("REDIS_SERVICE_HOST", "localhost"),
        port=int(getenv("REDIS_SERVICE_PORT", "6379")),
        db=0,
        decode_responses=True,
    )
    clean_up_empty_tasks(db)
