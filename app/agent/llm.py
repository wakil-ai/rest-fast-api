from crewai import LLM
from app.core.config import settings

if settings.LLM_PROVIDER == "novita":
    main_llm = LLM(
        # api_base=settings.NOVITA_API_BASE,
        model=f"novita/{settings.NOVITA_MODEL}",
        temperature=settings.TEMPERATURE,
        api_key=settings.NOVITA_API_KEY,
        timeout=120,    
    )
    tiny_llm = 'openai/gpt-4o-mini'  # Placeholder for actual LLM initialization
else:
    main_llm = 'openai/gpt-4o'  # Placeholder for actual LLM initialization
    tiny_llm = 'openai/gpt-4o-mini'