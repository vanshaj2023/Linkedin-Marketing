import inngest
from config import config

inngest_client = inngest.Inngest(
    app_id="linkedin-automation",
    signing_key=config.INNGEST_SIGNING_KEY if config.INNGEST_SIGNING_KEY else None,
    is_production=bool(config.INNGEST_SIGNING_KEY),
)
