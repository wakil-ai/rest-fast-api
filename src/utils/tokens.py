import tiktoken

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
