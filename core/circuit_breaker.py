import datetime
from db import db, SystemHealth


class CircuitBreaker:
    @staticmethod
    async def status() -> str:
        health = await db.system_health.find_one({"_id": "circuit_breaker"})
        if not health:
            await db.system_health.insert_one(SystemHealth().model_dump(by_alias=True))
            return "green"

        if health["status"] in ("yellow", "red") and health.get("auto_resume_at"):
            if datetime.datetime.utcnow() >= health["auto_resume_at"]:
                await CircuitBreaker.reset()
                return "green"

        return health["status"]

    @staticmethod
    async def trip(level: str, reason: str, auto_resume_hours: int = None):
        updates = {
            "status": level,
            "triggered_at": datetime.datetime.utcnow(),
            "reason": reason,
        }
        if auto_resume_hours:
            updates["auto_resume_at"] = (
                datetime.datetime.utcnow() + datetime.timedelta(hours=auto_resume_hours)
            )
        else:
            updates["auto_resume_at"] = None

        await db.system_health.update_one(
            {"_id": "circuit_breaker"}, {"$set": updates}, upsert=True
        )
        print(f"CRITICAL: Circuit breaker tripped to {level.upper()}! Reason: {reason}")

    @staticmethod
    async def reset():
        await db.system_health.update_one(
            {"_id": "circuit_breaker"},
            {"$set": {"status": "green", "auto_resume_at": None, "reason": "Reset"}},
        )
        print("System health reset to green.")
