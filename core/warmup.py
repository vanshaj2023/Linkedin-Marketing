import datetime
from db import db, DailyBudgets, DailyBudgetLimit

WARMUP_SCHEDULE = {
    1: {"connections": 5, "likes": 10, "views": 15, "comments": 2},
    2: {"connections": 12, "likes": 25, "views": 40, "comments": 5},
    3: {"connections": 20, "likes": 45, "views": 70, "comments": 8},
    4: {"connections": 25, "likes": 60, "views": 80, "comments": 10},
}


async def apply_warmup_budget(week: int = 1):
    week = min(week, 4)
    limits = WARMUP_SCHEDULE[week]
    today_str = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    record = await db.daily_budgets.find_one({"date": today_str})
    if not record:
        budget = DailyBudgets(
            date=today_str,
            connection_requests=DailyBudgetLimit(limit=limits["connections"]),
            profile_views=DailyBudgetLimit(limit=limits["views"]),
            likes=DailyBudgetLimit(limit=limits["likes"]),
            comments=DailyBudgetLimit(limit=limits["comments"]),
            reposts=DailyBudgetLimit(limit=3),
            searches=DailyBudgetLimit(limit=30),
        )
        await db.daily_budgets.insert_one(budget.model_dump(by_alias=True))
        print(f"Initialized {today_str} — Warmup Week {week}")
    else:
        await db.daily_budgets.update_one(
            {"date": today_str},
            {
                "$set": {
                    "connection_requests.limit": limits["connections"],
                    "profile_views.limit": limits["views"],
                    "likes.limit": limits["likes"],
                    "comments.limit": limits["comments"],
                }
            },
        )
        print(f"Updated {today_str} — Warmup Week {week}")
