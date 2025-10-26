from crewai import LLM
from app.core.config import settings

main_llm = LLM(
    api_base=settings.NOVITA_API_BASE,
    model=f"novita/{settings.NOVITA_MODEL}",
    temperature=settings.TEMPERATURE,
    api_key=settings.NOVITA_API_KEY,
)

tiny_llm = LLM(
    api_base=settings.NOVITA_API_BASE,
    model=f"novita/{settings.NOVITA_TINY_MODEL}",
    temperature=settings.TEMPERATURE,
    api_key=settings.NOVITA_API_KEY,
)

main_llm = 'gpt-4o'
tiny_llm = 'gpt-4o-mini'