import json
from pydantic import BaseModel, Field
from typing import List, Optional
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

class TargetProfile(BaseModel):
    name: str = Field(description="Name of the person")
    role: str = Field(description="Job title or role (e.g., Founder, Senior Engineer, Hiring Manager) based on the context.")
    profile_url: Optional[str] = Field(description="LinkedIn profile URL if available natively, else empty.")
    connection_note: str = Field(description="A highly personalized 1-2 sentence connection note referencing the post or hiring logic. Keep it under 200 characters.")

class PostEvaluation(BaseModel):
    is_relevant: bool = Field(description="True if this post is legitimately about tech hiring, open roles, or engineering networking.")
    reasoning: str = Field(description="Brief reason for why this post is or is not relevant.")
    suggested_comment: Optional[str] = Field(description="A context-aware, thoughtful, professional comment to leave on the post. Do not use hashtags. None if irrelevant.")
    high_value_targets: List[TargetProfile] = Field(description="List of relevant profiles (author, founders, senior engineers found in the text or comments) to connect with.")

def evaluate_post(post_data: dict, openai_api_key: str) -> dict:
    """
    Takes a single post dictionary scraped by scraper.py, and uses OpenAI to evaluate it.
    Returns structured JSON containing connection targets and suggested comments.
    """
    # Using a low temperature for predictable, professional outputs
    llm = ChatOpenAI(model="gpt-4o", temperature=0.2, api_key=openai_api_key)
    structured_llm = llm.with_structured_output(PostEvaluation)

    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are an AI assistant helping a software engineer network on LinkedIn. 
Your goal is to evaluate LinkedIn posts to see if they are relevant to job searching, hiring, or networking (especially in tech/engineering).
You will receive the post content, the author's details, and any visible comments.

If the post is relevant:
1. Generate a polite, engaging comment to leave on the post.
2. Identify high-value targets mentioned in the post, the author, or in the comments (e.g., Founders, CTOs, Senior Engineers, Recruiters).
3. Draft a short, personalized connection note for each high-value target (e.g., "Hi [Name], loved your post about [topic]. I'm a software engineer and would love to connect.").

If the post is not relevant (e.g., non-tech sales spam, generic motivational quotes), mark is_relevant as false.
"""),
        ("human", "Author: {author_name} ({author_url})\n\nPost Content:\n{content}\n\nVisible Comments:\n{visible_comments_text}")
    ])

    chain = prompt | structured_llm
    
    try:
        result = chain.invoke({
            "author_name": post_data.get("author_name", "Unknown"),
            "author_url": post_data.get("author_url", "Unknown"),
            "content": post_data.get("content", ""),
            "visible_comments_text": post_data.get("visible_comments_text", "")
        })
        return result.model_dump()
    except Exception as e:
        print(f"LLM Evaluation bypassed or failed ({e}). Returning MOCK response.")
        return {
            "is_relevant": True,
            "reasoning": "MOCK REASONING: Simulating a match so the bot can proceed to test connection and comments.",
            "suggested_comment": "This sounds like an incredible opportunity! I would love to connect and learn more.",
            "high_value_targets": [
                {
                    "name": post_data.get("author_name", "Author"),
                    "role": "Poster",
                    "profile_url": post_data.get("author_url", ""),
                    "connection_note": f"Hi, I saw your post about hiring and would love to connect to discuss potential engineering matches!"
                }
            ]
        }

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    
    # Load API key from local .env file
    load_dotenv()
    
    # Example Test Wrapper
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        try:
            with open("scraped_posts.json", "r", encoding="utf-8") as f:
                posts = json.load(f)
                
            if posts:
                print("Evaluating first post...")
                eval_result = evaluate_post(posts[0], api_key)
                print(json.dumps(eval_result, indent=2))
            else:
                print("No posts found in scraped_posts.json to evaluate.")
        except FileNotFoundError:
            print("Run scraper.py first to generate scraped_posts.json")
    else:
        print("OPENAI_API_KEY environment variable not set. Cannot run evaluation test.")
