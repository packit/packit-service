from os import getenv
from redis import Redis
import json

# We don't need this at the moment.
# It's here just in case you ever wanted to fix some values in Redis.
# https://github.com/packit-service/packit-service/issues/212


def fix_enum_values_github_installation(db):
    for k, v in db.hgetall("github_installation").items():
        value_dict = json.loads(v)

        trigger = value_dict.get("event_data", {}).get("trigger")
        if isinstance(trigger, str) and trigger.startswith("JobTriggerType."):
            new_trigger = trigger.split(".", 1)[1]
            print(f"{k}: {trigger} -> {new_trigger}")
            value_dict["event_data"]["trigger"] = new_trigger
            db.hset("github_installation", k, json.dumps(value_dict))


def fix_enum_values_whitelist(db):
    for k, v in db.hgetall("whitelist").items():
        value_dict = json.loads(v)

        trigger = value_dict.get("trigger")
        if isinstance(trigger, str) and trigger.startswith("JobTriggerType."):
            new_trigger = trigger.split(".", 1)[1]
            print(f"{k}: {trigger} -> {new_trigger}")
            value_dict["trigger"] = new_trigger
            db.hset("whitelist", k, json.dumps(value_dict))


if __name__ == "__main__":
    db = Redis(
        host=getenv("REDIS_SERVICE_HOST", "localhost"),
        port=int(getenv("REDIS_SERVICE_PORT", "6379")),
        db=1,
        decode_responses=True,
    )
    fix_enum_values_github_installation(db)
    fix_enum_values_whitelist(db)
