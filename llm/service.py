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


def generate_engage_comment(author_name: str, post_content: str) -> str:
    """Generate a short, genuine comment for engagement."""
    result = _chat([
        {"role": "system", "content": "Write a genuine, short LinkedIn comment (1-2 sentences max). No hashtags. Sound human, not corporate. Return only the comment text."},
        {"role": "user", "content": f"Post by {author_name}: {post_content[:500]}"},
    ])
    return result.strip()
