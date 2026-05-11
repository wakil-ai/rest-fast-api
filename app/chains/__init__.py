"""Chain-adjacent classifiers and prompt registries.

Imports stay lazy so importing one module, such as ``app.chains.court_classifier``,
does not pull in every optional LangChain parser dependency.
"""

from app.chains.chat_orchestrator import ChatOrchestrator

__all__ = ["ChatOrchestrator"]
