import os
import certifi
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from datetime import datetime

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGODB_URI, tlsCAFile=certifi.where())
db = client.linkedin_automation


# ── Schema Definitions ───────────────────────────────────────────────────────

class ActionQueueItem(BaseModel):
    agent: str
    action_type: str
    payload: dict[str, Any]
    priority: int = 5
    status: str = "queued"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    executed_at: Optional[datetime] = None
    retry_count: int = 0
    error: Optional[str] = None
    dry_run: bool = False


class DailyBudgetLimit(BaseModel):
    used: int = 0
    limit: int


class DailyBudgets(BaseModel):
    date: str
    connection_requests: DailyBudgetLimit = DailyBudgetLimit(limit=25)
    profile_views: DailyBudgetLimit = DailyBudgetLimit(limit=80)
    likes: DailyBudgetLimit = DailyBudgetLimit(limit=60)
    comments: DailyBudgetLimit = DailyBudgetLimit(limit=10)
    reposts: DailyBudgetLimit = DailyBudgetLimit(limit=3)
    searches: DailyBudgetLimit = DailyBudgetLimit(limit=30)


class SystemHealth(BaseModel):
    id: str = Field(default="circuit_breaker", alias="_id")
    status: str = "green"
    triggered_at: Optional[datetime] = None
    reason: Optional[str] = None
    auto_resume_at: Optional[datetime] = None


class Connection(BaseModel):
    linkedin_url: str
    name: str
    headline: str
    company: str
    status: str = "identified"
    source_agent: str
    relevance_score: Optional[int] = None
    personalization_note: Optional[str] = None
    connected_at: Optional[datetime] = None
    first_contacted_at: Optional[datetime] = None
    last_action_at: Optional[datetime] = None
    tags: List[str] = []


class Job(BaseModel):
    linkedin_post_url: str
    job_title: str
    company: str
    poster_name: str
    relevance_score: Optional[int] = None
    action_taken: str = "none"
    comment_text: Optional[str] = None
    slack_message_ts: Optional[str] = None
    discovered_at: datetime = Field(default_factory=datetime.utcnow)
    applied: bool = False
    applied_at: Optional[datetime] = None


class EngageListMember(BaseModel):
    linkedin_url: str
    name: str
    reason: str
    last_post_url: Optional[str] = None
    last_engaged_at: Optional[datetime] = None
    engagement_count: int = 0
    auto_comment: bool = True
    added_by_agent: str


class ReferralTarget(BaseModel):
    linkedin_url: str
    name: str
    role: str
    score: int
    batch: int
    connection_status: str = "pending"
    posts_liked: int = 0
    referral_email_sent: bool = False
    referral_email_sent_at: Optional[datetime] = None
    response_received: bool = False
    notes: Optional[str] = None


class ReferralCampaign(BaseModel):
    campaign_id: str
    company: str
    target_role: str
    job_post_url: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    targets: List[ReferralTarget] = []


class ReputationScore(BaseModel):
    linkedin_url: str
    acceptance_rate: float = 0.0
    response_rate: float = 0.0
    referral_conversion: bool = False
    engagement_reciprocity: float = 0.0
    outreach_template_used: Optional[str] = None
    company: Optional[str] = None
    role_type: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ── Indexes ──────────────────────────────────────────────────────────────────

async def setup_indexes():
    await db.action_queue.create_index([("priority", 1), ("created_at", 1)])
    await db.action_queue.create_index("status")
    await db.daily_budgets.create_index("date", unique=True)
    await db.connections.create_index("linkedin_url", unique=True)
    await db.jobs.create_index("linkedin_post_url", unique=True)
    await db.engage_list.create_index("linkedin_url", unique=True)
    await db.referral_campaigns.create_index("campaign_id", unique=True)
    await db.reputation_scores.create_index("linkedin_url", unique=True)
