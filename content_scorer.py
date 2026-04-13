from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from typing import Optional

class PostEvaluation(BaseModel):
    score: int = Field(description="Score 0-100 evaluating the quality and relevance of the post for reposting. Based on author relevance, content quality, and hook.")
    reasoning: str = Field(description="Why this score was chosen.")
    auto_repost: bool = Field(description="Whether the score is 90 or above, indicating it should be automatically reposted safely.")
    suggested_repost_caption: Optional[str] = Field(description="If we repost, what is a 1-sentence supportive caption we can add? E.g., 'Great insights on this topic by [Name].'")

class CommentGeneration(BaseModel):
    is_good_hook: bool = Field(description="Does this post have a strong enough hook/conversation-starter to warrant a thoughtful comment?")
    comment_text: str = Field(description="The generated thoughtful comment text (<280 characters), or empty if none.")

llm = ChatOpenAI(temperature=0.7, model="gpt-4o")

async def score_post_for_repost(post_text: str, author_name: str, target_domain: str) -> dict:
    """Evaluates a feed post to see if it makes a good repost candidate."""
    evaluator_llm = llm.with_structured_output(PostEvaluation)
    
    prompt = f"""
    You are an expert LinkedIn strategist. Evaluate the following post to see if your client (who operates in the {target_domain} space) should repost it.
    
    Author: {author_name}
    Post Content: {post_text}
    
    Task:
    1. Give it a score from 0-100. Be strict. Only give 90+ if it is exceptionally well-written, viral-worthy, and highly relevant to {target_domain}.
    2. Suggest a short 1-sentence caption for the repost.
    """
    
    response: PostEvaluation = await evaluator_llm.ainvoke(prompt)
    return response.model_dump()

async def generate_thoughtful_comment(post_text: str, author_name: str) -> dict:
    """Evaluates a post to see if we should drop a thoughtful comment to keep engagement high."""
    evaluator_llm = llm.with_structured_output(CommentGeneration)
    
    prompt = f"""
    You are a professional LinkedIn user maintaining a relationship with '{author_name}'.
    They just posted: {post_text}
    
    Task:
    1. Is this post engaging enough to warrant a comment? (e.g., asking a question, sharing a big milestone, controversial take).
    2. If yes, write a thoughtful, professional, conversational comment under 200 characters. Do not sound like an AI. Do not use hashtags.
    """
    
    response: CommentGeneration = await evaluator_llm.ainvoke(prompt)
    return response.model_dump()
