# Development Mode Feature

## Overview
Development mode allows the backend to send retrieved contents and processing logs to the UI for debugging and analysis purposes.

## Configuration

Add the following to your `.env` file:

```bash
DEVELOPMENT_MODE=true  # or false to disable
```

Default value is `false` if not specified.

## Affected Endpoints

When `DEVELOPMENT_MODE=true`, the following endpoints will include additional debug information in their responses:

- `POST /api/chat/ask`
- `POST /api/chat/soliq`
- `POST /api/chat/file`

## Response Format

### Normal Mode (DEVELOPMENT_MODE=false)

#### Non-Streaming Response
```json
{
  "answer": "The answer to your question..."
}
```

#### Streaming Response (SSE)
```
data: {"type": "chunk", "chunk": "T"}

data: {"type": "chunk", "chunk": "h"}

data: {"type": "chunk", "chunk": "e"}

...

data: {"type": "end"}
```

### Development Mode (DEVELOPMENT_MODE=true)

#### Non-Streaming Response
```json
{
  "answer": "The answer to your question...",
  "retrieved_contents": [
    {
      "content": "Document text content",
      "metadata": {
        "text": "...",
        "url": "...",
        "hierarchy_path": "...",
        "date": "...",
        "document_number": "..."
      },
      "distance": 0.234
    }
  ],
  "logs": [
    "Calling internal LLM service",
    "Loading uploaded/project context",
    "Persisting generated response",
    "Response generated successfully"
  ]
}
```

#### Streaming Response (SSE)
```
data: {"type": "debug", "data": {"retrieved_contents": [...], "logs": [...]}}

data: {"type": "chunk", "chunk": "T"}

data: {"type": "chunk", "chunk": "h"}

data: {"type": "chunk", "chunk": "e"}

...

data: {"type": "end"}
```

**Note**: In streaming mode, debug data is sent as the **first event** with `type: "debug"` before any text chunks.

## Debug Information

### retrieved_contents
An array of retrieved documents with:
- **content**: The actual text content of the document
- **metadata**: Document metadata including URL, hierarchy path, date, etc.
- **distance**: Similarity score (lower = more relevant)

### logs
An array of processing steps including:
- LLM selection and usage
- Context retrieval information
- Memory retrieval status
- Error messages (if any occur)
- Success confirmations

## Usage Notes

1. **Performance**: Development mode adds minimal overhead as it only collects data that's already being processed
2. **Streaming**: Debug information IS included in streaming responses as the first SSE event with `type: "debug"`
3. **Production**: Always set `DEVELOPMENT_MODE=false` in production environments
4. **Security**: Debug information may contain sensitive data from retrieved documents
5. **Client-side**: When consuming streaming responses, handle the `debug` event type to capture debug data before processing text chunks

## Implementation Details

The feature is implemented across several layers:

1. **Config** ([app/core/config.py](app/core/config.py)): `DEVELOPMENT_MODE` flag
2. **Models** ([app/models/chat.py](app/models/chat.py)): Extended `ChatResponse` with optional debug fields
3. **Retrieval** ([app/retrieval/retrieval_service.py](app/retrieval/retrieval_service.py)): Returns raw document metadata when requested
4. **Assistant Agents** ([app/agents/base.py](../app/agents/base.py)): Own chat-turn context, retrieval, and generation flow
5. **API** ([app/api/chat.py](app/api/chat.py)): Includes debug data in responses

## Example Usage

### Non-Streaming Request
```python
import requests

# Make a request
response = requests.post(
    "http://localhost:8000/api/chat/ask",
    json={
        "user_id": "user_123",
        "query": "What are the marriage laws?",
        "stream": False
    }
)

result = response.json()

# Access debug information (only available when DEVELOPMENT_MODE=true)
if "retrieved_contents" in result:
    print(f"Retrieved {len(result['retrieved_contents'])} documents")
    for doc in result['retrieved_contents']:
        print(f"- {doc['metadata'].get('url', 'No URL')}")

if "logs" in result:
    print("\nProcessing logs:")
    for log in result['logs']:
        print(f"  {log}")
```

### Streaming Request
```python
import requests
import json

# Make a streaming request
response = requests.post(
    "http://localhost:8000/api/chat/ask",
    json={
        "user_id": "user_123",
        "query": "What are the marriage laws?",
        "stream": True
    },
    stream=True
)

debug_data = None
answer = ""

# Parse SSE events
for line in response.iter_lines():
    if line:
        line = line.decode('utf-8')
        if line.startswith('data: '):
            data = json.loads(line[6:])  # Remove 'data: ' prefix
            
            if data['type'] == 'debug':
                # First event contains debug data (if DEVELOPMENT_MODE=true)
                debug_data = data['data']
                print(f"Retrieved {len(debug_data['retrieved_contents'])} documents")
                print(f"Logs: {debug_data['logs']}")
            
            elif data['type'] == 'chunk':
                # Regular text chunk
                answer += data['chunk']
                print(data['chunk'], end='', flush=True)
            
            elif data['type'] == 'end':
                # Stream completed
                print("\n\nStream completed")
                break
            
            elif data['type'] == 'error':
                # Error occurred
                print(f"\nError: {data['error']}")
                break

print(f"\n\nFull answer: {answer}")
if debug_data:
    print(f"Debug data available: {len(debug_data['logs'])} log entries")
```
