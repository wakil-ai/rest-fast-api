from typing import Any

import tiktoken

from app.core.logger import logger

encoding = tiktoken.encoding_for_model("gpt-4.1")


def count_tokens(text: str) -> int:
    """Count the number of tokens in a given text for a specified model."""
    tokens = encoding.encode(text)
    return len(tokens)


def truncate_to_token_limit(text: str, max_tokens: int) -> str:
    """Truncate the text to fit within the specified token limit."""
    tokens = encoding.encode(text)
    if len(tokens) <= max_tokens:
        return text
    truncated_tokens = tokens[:max_tokens]
    return encoding.decode(truncated_tokens)


def cap_invoke_input(value: Any, max_tokens: int) -> Any:
    """Cap an LLM invoke input to ``max_tokens`` (shared context controller).

    Handles the two shapes every ``traced_ainvoke`` caller uses:
      * ``str`` prompts (e.g. the query-rewrite agent) -> head-truncated, which
        keeps the leading instructions + query and drops trailing bulk.
      * ``list[BaseMessage]`` (classification agents: system + user) -> each
        string content is shrunk proportionally to its share of the budget, so
        all messages/roles survive and the largest (usually the system prompt
        carrying file/history context) is trimmed the most.

    Returns the input unchanged when it already fits, when capping is disabled
    (``max_tokens <= 0``), or for shapes it does not recognize.
    """
    if max_tokens <= 0 or value is None:
        return value

    if isinstance(value, str):
        size = count_tokens(value)
        if size <= max_tokens:
            return value
        logger.warning(
            "[ContextController] invoke input (str) {} tok > cap {} tok; truncated to {}",
            size,
            max_tokens,
            max_tokens,
        )
        return truncate_to_token_limit(value, max_tokens)

    if isinstance(value, list) and value:
        from langchain_core.messages import BaseMessage

        if not all(isinstance(m, BaseMessage) for m in value):
            return value
        sizes = [
            count_tokens(m.content) if isinstance(m.content, str) else 0
            for m in value
        ]
        total = sum(sizes)
        if total <= max_tokens:
            return value
        capped: list[Any] = []
        for message, size in zip(value, sizes):
            content = message.content
            if isinstance(content, str) and size:
                share = max(1, int(max_tokens * size / total))
                if size > share:
                    message = message.model_copy(
                        update={"content": truncate_to_token_limit(content, share)}
                    )
            capped.append(message)
        logger.warning(
            "[ContextController] invoke input ({} messages) {} tok > cap {} tok; "
            "shrunk message contents to fit",
            len(value),
            total,
            max_tokens,
        )
        return capped

    return value
