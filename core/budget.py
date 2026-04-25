import datetime
from db import db, DailyBudgets


class BudgetManager:
    @staticmethod
    async def _get_today() -> dict:
        today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        record = await db.daily_budgets.find_one({"date": today_str})
        if not record:
            new_budget = DailyBudgets(date=today_str).model_dump(by_alias=True)
            await db.daily_budgets.insert_one(new_budget)
            return new_budget
        return record

    @staticmethod
    async def check_budget(action_key: str) -> bool:
        today = await BudgetManager._get_today()
        budget = today.get(action_key, {})
        return budget.get("used", 0) < budget.get("limit", 1)

    @staticmethod
    async def increment_budget(action_key: str):
        today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        await db.daily_budgets.update_one(
            {"date": today_str},
            {"$inc": {f"{action_key}.used": 1}},
            upsert=True,
        )
