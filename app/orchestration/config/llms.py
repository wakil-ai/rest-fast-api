from crewai import LLM
from app.core.config import settings

main_llm = LLM(
    model='openai/gpt-4.1',
    temperature=settings.TEMPERATURE,
    stream=True,
    reasoning_effort='high'
)

tiny_llm = LLM(
    model='openai/gpt-4.1-mini',
    temperature=0.0, # deterministic
    stream=False
)
