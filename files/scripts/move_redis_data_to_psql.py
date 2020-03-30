from packit_service.models import TaskResultModel, WhitelistModel, get_sa_session
from packit_service.service.models import Installation

from celery.backends.database.models import Task
from json import loads
from os import getenv
from redis import Redis
from persistentdict.dict_in_redis import PersistentDict


def add_task_to_celery_table(task_id, status, result, traceback, date_done):
    with get_sa_session() as session:
        task_result = session.query(Task).filter_by(task_id=task_id).first()
        if task_result is None:
            task_result = Task(task_id)
        task_result.status = status
        task_result.result = result
        task_result.traceback = traceback
        task_result.date_done = date_done
        session.add(task_result)


def move_tasks():
    db = Redis(
        host=getenv("REDIS_SERVICE_HOST", "localhost"),
        port=int(getenv("REDIS_SERVICE_PORT", "6379")),
        db=0,
        decode_responses=True,
    )
    keys = db.keys("celery-task-meta-*")
    for key in keys:
        data = loads(db.get(key))
        task_id = data.get("task_id")
        status = data.get("status")
        result = data.get("result")
        traceback = data.get("traceback")
        date_done = data.get("data_done")

        # our table
        TaskResultModel.add_task(task_id, result)
        # celery table
        add_task_to_celery_table(task_id, status, result, traceback, date_done)


def move_whitelist():
    db = PersistentDict(hash_name="whitelist")
    for account, data in db.get_all():
        status = data.get("status")
        WhitelistModel.add_account(account, status)


def move_installations():
    for installation_id, event in Installation.db().get_all():
        pass


if __name__ == "__main__":
    move_tasks()
    move_whitelist()
    move_installations()
