from dataclasses import dataclass
from typing import Annotated, Any, Optional

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


COLLECTION_KEYS: dict[str, str] = {
    "customs": "customs_legal_docs",
    "tax": "tax",
    "civil": "civil_legal_docs",
    "uploaded": "uploaded_user_docs",
}


@dataclass
class GraphContext:
    user_id: str


class RetrievalRewriteState(TypedDict, total=False):
    query: str
    user_id: str
    project_id: str
    session_id: str

    collection_name: str
    assistant_name: str
    deep_research: bool

    messages: Annotated[list[BaseMessage], add_messages]
    file_context: Optional[str]
    project_related_context: Optional[str]
    resolved_file_ids: list[str]
    resolved_project_id: Optional[str]
    long_term_memory: str

    rewritten_query: str
    intent_domain: str
    legal_intent: str
    selected_assistant: str
    court_route_tag: Optional[str]
    milvus_filter: str

    retrieval_context: str
    answer_prompt_template: Any
    attachments: list[dict[str, Any]]
    classified_legal_intent: str
    file_ids: list[str]
    message_id: str
    context_evaluation_output: dict[str, Any]
    web_search_output: dict[str, Any]

    final_answer: str
