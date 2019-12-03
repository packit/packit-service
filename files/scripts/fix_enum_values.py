from os import getenv
from redis import Redis
import json


# https://github.com/packit-service/packit-service/issues/212
def fix_enum_values(db, hash):
    for k, v in db.hgetall(hash).items():
        value_dict = json.loads(v)

        if (
            "event_data" in value_dict.keys()
            and "trigger" in value_dict["event_data"].keys()
        ):
            trigger = value_dict["event_data"]["trigger"]
            if "JobTriggerType" in trigger:
                new_trigger = trigger.split(".", 1)[1]
                value_dict["event_data"]["trigger"] = new_trigger
                db.hset(hash, k, json.dumps(value_dict))


if __name__ == "__main__":
    db = Redis(
        host=getenv("REDIS_SERVICE_HOST", "localhost"),
        port=int(getenv("REDIS_SERVICE_PORT", "6379")),
        db=1,
        decode_responses=True,
    )
    fix_enum_values(db, "whitelist")
    fix_enum_values(db, "github_installation")
