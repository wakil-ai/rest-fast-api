from pydantic import BaseModel, Field


class StreamChunk(BaseModel):
    """
    Individual chunk in streaming response.
    """

    chunk: str = Field(..., description="Text chunk from the streaming response")


class StreamEndSignal(BaseModel):
    """
    Signal indicating the end of streaming response.
    """

    done: bool = Field(default=True, description="Indicates streaming is complete")


class StreamError(BaseModel):
    """
    Error message in streaming response.
    """

    error: str = Field(..., description="Error message")
