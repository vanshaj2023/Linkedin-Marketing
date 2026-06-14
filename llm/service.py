import json
from groq import Groq
from config import config

_groq = Groq(api_key=config.GROQ_API_KEY)


def _chat(messages: list, json_mode: bool = False) -> str:
    kwargs = {
        "model": config.GROQ_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1024,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = _groq.chat.completions.create(**kwargs)
    return response.choices[0].message.content


def generate_connection_note(headline: str, post_summary: str, template_id: str = "A") -> str:
    """Generate a personalised LinkedIn connection note (<=280 chars)."""
    templates = {
        "A": f"Given headline: '{headline}' and topic: '{post_summary}', write a 280-char connection note referencing their work, mentioning shared interest in {config.YOUR_DOMAIN}, casual and human, NOT salesy, does NOT ask for anything. Reply with just the note text.",
        "B": f"Write a brief 280-char LinkedIn connection note to someone with headline '{headline}'. Their work relates to '{post_summary}'. Be genuine, mention {config.YOUR_DOMAIN}. No hashtags. No ask. Just the note.",
        "C": f"Craft a short LinkedIn connection request note (<=280 chars). Recipient headline: '{headline}'. Context: '{post_summary}'. Be specific, no fluff, no ask, relate to {config.YOUR_DOMAIN}.",
        "D": f"Write a personalized 280-char LinkedIn note for someone in '{headline}' related to '{post_summary}'. Sound like a real person interested in {config.YOUR_DOMAIN}.",
        "E": f"Short LinkedIn note (<=280 chars) to connect with '{headline}'. Context: '{post_summary}'. Reference their work. Mention {config.YOUR_DOMAIN}. Casual, no sales pitch.",
    }
    prompt = templates.get(template_id, templates["A"])
    result = _chat([
        {"role": "system", "content": "You write concise, natural-sounding LinkedIn connection notes. Return ONLY the note text, nothing else."},
        {"role": "user", "content": prompt},
    ])
    return result.strip()[:280]


def score_connection_profile(headline: str, company: str, mutual_connections: int) -> int:
    """Return a relevance score 0-100 for a LinkedIn profile."""
    result = _chat([
        {"role": "system", "content": "You are evaluating LinkedIn profiles for a software engineer looking to network. Return JSON with keys: score (int 0-100), reason (string)."},
        {"role": "user", "content": f"Headline: '{headline}', company: '{company}', mutual connections: {mutual_connections}. Target domain: {config.YOUR_DOMAIN}. Score relevance for networking/job referral."},
    ], json_mode=True)
    try:
        return int(json.loads(result).get("score", 0))
    except Exception:
        return 0


def score_job_post(title: str, company: str, description: str, poster_text: str) -> dict:
    """Score a job post. Returns: relevance_score, should_comment_email, comment_text, reasoning, company_for_referral."""
    result = _chat([
        {"role": "system", "content": "You are helping a software engineer find relevant jobs. Analyze job posts and return JSON with keys: relevance_score (int 0-100), should_comment_email (bool), comment_text (string or null), reasoning (string), company_for_referral (company name string or null if score < 80)."},
        {"role": "user", "content": f"Title: {title}\nCompany: {company}\nDescription: {description[:800]}\nPoster note: {poster_text}"},
    ], json_mode=True)
    try:
        return json.loads(result)
    except Exception:
        return {"relevance_score": 0, "should_comment_email": False, "comment_text": None, "reasoning": "parse error", "company_for_referral": None}


def score_post_for_repost(author_name: str, content: str, likes: int, comments: int, hours_old: float) -> dict:
    """Score a feed post for repost value. Returns: score, reasoning, suggested_caption."""
    result = _chat([
        {"role": "system", "content": f"You help a software engineer decide which LinkedIn posts to repost. Domain: {config.YOUR_DOMAIN}. Score posts 0-100. Return JSON with keys: score (int), reasoning (string), suggested_caption (string)."},
        {"role": "user", "content": f"Author: {author_name}\nPost: {content[:600]}\nLikes: {likes}, Comments: {comments}, Posted {hours_old:.1f}h ago."},
    ], json_mode=True)
    try:
        return json.loads(result)
    except Exception:
        return {"score": 0, "reasoning": "parse error", "suggested_caption": ""}


def classify_feed_post_as_job(content: str, author_name: str) -> dict:
    """Classify whether a feed post is a hiring/job/referral signal.

    Returns JSON with keys: is_job_post, role, company, apply_method,
    apply_target, relevance_score (0-100), reasoning.
    apply_method: "comment" | "dm" | "link" | "email" | "unknown"
    """
    result = _chat([
        {
            "role": "system",
            "content": (
                "You analyze LinkedIn posts to detect job/hiring signals for a software engineer. "
                f"The engineer specializes in: {config.YOUR_DOMAIN}. "
                "Return JSON with these exact keys: "
                "is_job_post (bool), "
                "role (string or null — job title if mentioned), "
                "company (string or null), "
                "apply_method (one of: comment, dm, link, email, unknown), "
                "apply_target (string or null — the URL/email/instruction to apply), "
                "relevance_score (int 0-100 — how relevant is this role to the engineer), "
                "reasoning (string — 1 sentence why)."
            ),
        },
        {
            "role": "user",
            "content": f"Author: {author_name}\n\nPost:\n{content[:800]}",
        },
    ], json_mode=True)
    try:
        data = json.loads(result)
        return {
            "is_job_post": bool(data.get("is_job_post", False)),
            "role": data.get("role"),
            "company": data.get("company"),
            "apply_method": data.get("apply_method", "unknown"),
            "apply_target": data.get("apply_target"),
            "relevance_score": int(data.get("relevance_score", 0)),
            "reasoning": data.get("reasoning", ""),
        }
    except Exception:
        return {
            "is_job_post": False, "role": None, "company": None,
            "apply_method": "unknown", "apply_target": None,
            "relevance_score": 0, "reasoning": "parse error",
        }


def answer_application_question(
    question: str,
    field_type: str,
    options: list[str] | None = None,
    context: dict | None = None,
) -> str:
    """Answer a job application form field using applicant profile context.

    field_type: "text" | "number" | "yes_no" | "single_select" | "multi_select"
    For select types, the returned value must exactly match one of options.
    """
    ctx = context or {}
    profile_block = (
        f"Applicant profile:\n"
        f"- Name: {config.YOUR_NAME}\n"
        f"- Role: {ctx.get('current_role', config.YOUR_DOMAIN)}\n"
        f"- Years experience: {ctx.get('years_experience', 2)}\n"
        f"- Requires sponsorship: {ctx.get('requires_sponsorship', 'No')}\n"
        f"- Willing to relocate: {ctx.get('willing_to_relocate', 'No')}\n"
        f"- Salary expectation: {ctx.get('salary_expectation', 'Negotiable')}\n"
        f"- LinkedIn: {ctx.get('linkedin_url', '')}\n"
        f"- GitHub: {ctx.get('github_url', '')}\n"
    )
    options_block = f"\nAllowed values (pick exactly one): {options}" if options else ""
    result = _chat([
        {
            "role": "system",
            "content": (
                "You fill job application forms accurately and concisely on behalf of an applicant. "
                "Return ONLY the answer — no explanation, no quotes, no extra text. "
                "For yes/no or select questions, return exactly one of the allowed values."
            ),
        },
        {
            "role": "user",
            "content": f"{profile_block}\n\nQuestion: {question}\nField type: {field_type}{options_block}",
        },
    ])
    return result.strip()


def generate_referral_message(
    to_name: str, to_headline: str, company: str, target_role: str = "",
) -> str:
    """Generate a polite LinkedIn DM asking for a referral. <=600 chars."""
    role_line = (
        f"There's a {target_role} role open at {company} that I'd love to be considered for."
        if target_role
        else f"I'm interested in opportunities at {company} that fit my background."
    )
    result = _chat([
        {"role": "system", "content": (
            "You write short, polite LinkedIn DMs that ask for a referral. "
            "Constraints: <=600 characters, conversational, no hashtags, no salesy phrasing. "
            "Open by thanking them for connecting, mention you saw their work, ask if they'd "
            "be open to a referral, give them an easy out. Sign off with the sender's first name only. "
            "Return ONLY the message text."
        )},
        {"role": "user", "content": (
            f"Recipient name: {to_name}\n"
            f"Recipient headline: {to_headline}\n"
            f"Target company: {company}\n"
            f"{role_line}\n"
            f"Sender domain: {config.YOUR_DOMAIN}\n"
            f"Sender name: {config.YOUR_NAME or 'me'}"
        )},
    ])
    return result.strip()[:600]


def generate_engage_comment(author_name: str, post_content: str) -> str:
    """Generate a short, genuine comment for engagement."""
    result = _chat([
        {"role": "system", "content": "Write a genuine, short LinkedIn comment (1-2 sentences max). No hashtags. Sound human, not corporate. Return only the comment text."},
        {"role": "user", "content": f"Post by {author_name}: {post_content[:500]}"},
    ])
    return result.strip()
