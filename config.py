import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- LLM ---
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_VISION_MODEL: str = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")

    # --- Slack ---
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN", "")
    SLACK_SIGNING_SECRET: str = os.getenv("SLACK_SIGNING_SECRET", "")

    # --- Gmail SMTP ---
    GMAIL_USER: str = os.getenv("GMAIL_USER", "")
    GMAIL_APP_PASSWORD: str = os.getenv("GMAIL_APP_PASSWORD", "")

    # --- MongoDB ---
    MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")

    # --- Inngest ---
    INNGEST_SIGNING_KEY: str = os.getenv("INNGEST_SIGNING_KEY", "")
    INNGEST_EVENT_KEY: str = os.getenv("INNGEST_EVENT_KEY", "")

    # --- System flags ---
    DRY_RUN: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    WARMUP_WEEK: int = int(os.getenv("WARMUP_WEEK", "1"))

    # --- Targeting ---
    TARGET_KEYWORDS: list = [
        k.strip() for k in os.getenv(
            "TARGET_KEYWORDS", "hiring,software engineer,backend developer"
        ).split(",")
    ]
    TARGET_JOB_KEYWORDS: list = [
        k.strip() for k in os.getenv(
            "TARGET_JOB_KEYWORDS", "backend engineer,software engineer,python developer"
        ).split(",")
    ]
    TARGET_JOB_LOCATIONS: list = [
        k.strip() for k in os.getenv(
            "TARGET_JOB_LOCATIONS", "India,Remote"
        ).split(",")
    ]
    AUTO_REPOST_SCORE_THRESHOLD: int = int(os.getenv("AUTO_REPOST_THRESHOLD", "90"))
    CONNECTION_RELEVANCE_THRESHOLD: int = int(os.getenv("CONNECTION_THRESHOLD", "60"))
    JOB_SLACK_NOTIFY_THRESHOLD: int = int(os.getenv("JOB_SLACK_THRESHOLD", "70"))

    # --- Your profile ---
    YOUR_DOMAIN: str = os.getenv("YOUR_DOMAIN", "backend engineering")
    YOUR_NAME: str = os.getenv("YOUR_NAME", "")
    YOUR_EMAIL: str = os.getenv("YOUR_EMAIL", "")

    # --- Applicant details for Easy Apply forms ---
    APPLICANT_PROFILE: dict = {
        "phone": os.getenv("APPLICANT_PHONE", ""),
        "years_experience": int(os.getenv("APPLICANT_YOE", "2")),
        "current_role": os.getenv("APPLICANT_ROLE", ""),
        "resume_path": os.getenv("APPLICANT_RESUME_PATH", "./resume.pdf"),
        "linkedin_url": os.getenv("APPLICANT_LINKEDIN_URL", ""),
        "github_url": os.getenv("APPLICANT_GITHUB_URL", ""),
        "salary_expectation": os.getenv("APPLICANT_SALARY", "Negotiable"),
        "willing_to_relocate": os.getenv("APPLICANT_RELOCATE", "false"),
        "requires_sponsorship": os.getenv("APPLICANT_SPONSORSHIP", "false"),
    }


config = Config()
