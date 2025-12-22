from crewai import LLM
from app.core.config import settings

if settings.LLM_PROVIDER == "novita":
    main_llm = 'openai/gpt-4.1'
    tiny_llm = 'openai/gpt-4o-mini' 
else:
    main_llm = 'openai/gpt-4.1'
    tiny_llm = 'openai/gpt-4o-mini'
    
