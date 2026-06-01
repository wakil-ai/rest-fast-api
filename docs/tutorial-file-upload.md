# Tutorial: Upload a File and Chat About It

In this tutorial you'll create a legal project workspace, upload a PDF contract, and ask questions about it. The API extracts text via OCR, embeds it into Milvus, and retrieves it as context alongside the standard legal corpus.

**Time to complete:** ~20 minutes  
**Prerequisites:** Complete [Tutorial: First Chat](tutorial-first-chat.md). You also need GCS configured (for file storage) and Datalab (for OCR).

---

## What you'll build

A workflow that:
1. Creates a legal project workspace.
2. Uploads a contract PDF.
3. Sends questions about the contract and gets answers grounded in its content.

---

## Step 1: Set up GCS and OCR

Add to `.env`:

```env
GCS_BUCKET_NAME=your-bucket-name
GCS_CREDENTIALS_PATH=/app/app/security/gcs_creds.json
GCS_PROJECT_ID=your-gcp-project-id

DATALAB_API_KEY=your-datalab-key
```

Place your GCS service account JSON at the path specified by `GCS_CREDENTIALS_PATH`.

Restart the API.

---

## Step 2: Create a legal project

A project is a workspace that groups sessions and files together under shared instructions.

```bash
curl -X POST http://localhost:8080/api/v2/history/projects \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "tutorial-user-1",
    "title": "Ijara shartnomasi tahlili",
    "description": "Ijara shartnomasini ko'\''rib chiqish uchun loyiha"
  }'
```

Note the `_id` — this is your `project_id`:

```json
{
  "_id": "proj_abc123",
  "user_id": "tutorial-user-1",
  "title": "Ijara shartnomasi tahlili",
  "created_at": "2026-05-21T..."
}
```

---

## Step 3: (Optional) Add project-level instructions

You can give the assistant custom instructions that apply to all sessions in this project:

```bash
curl -X PATCH http://localhost:8080/api/v2/history/projects/proj_abc123/instructions \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "instructions": "Bu loyihada ijara shartnomasini tahlil qilasiz. Har doim o'\''zbek tilida javob bering va aniq modda raqamlarini keltiring."
  }'
```

These instructions are loaded as `project_related_context` by the `load_file_and_project_context` node and prepended to every request in this project.

---

## Step 4: Create a session linked to the project

```bash
curl -X POST http://localhost:8080/api/v2/history/sessions \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "tutorial-user-1",
    "project_id": "proj_abc123",
    "title": "Ijara shartnomasi sessiyasi"
  }'
```

Note the session `_id`: `sess_proj_xyz`.

---

## Step 5: Upload the contract

Upload a PDF (or any supported document format):

```bash
curl -X POST http://localhost:8080/api/v2/history/files \
  -H "admin: dev-key" \
  -F "user_id=tutorial-user-1" \
  -F "project_id=proj_abc123" \
  -F "file=@/path/to/ijara_shartnomasi.pdf"
```

What happens internally:
1. PDF is uploaded to GCS at `projects/{project_id}/files/{file_id}/filename.pdf`.
2. Datalab OCR extracts text from the PDF.
3. Text is split into chunks and embedded via the configured embedding model.
4. Chunks are stored in Milvus `project_files` collection with `project_id` + `user_id` metadata.
5. File record is saved in MongoDB `files` collection.

The response includes the file ID:

```json
{
  "_id": "file_def456",
  "user_id": "tutorial-user-1",
  "project_id": "proj_abc123",
  "filename": "ijara_shartnomasi.pdf",
  "status": "processed",
  "created_at": "2026-05-21T..."
}
```

Processing is synchronous — the response arrives when OCR and embedding are complete.

---

## Step 6: Ask a question about the contract

Send a chat request with the `project_id`. The orchestration graph automatically retrieves relevant chunks from the project's Milvus files:

```bash
curl -X POST http://localhost:8080/api/v3/chat/ask \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "tutorial-user-1",
    "session_id": "sess_proj_xyz",
    "project_id": "proj_abc123",
    "query": "Shartnomada ijarachilar qanday majburiyatlari bor?",
    "assistant": "contract_analyzer",
    "stream": false
  }'
```

Expected response (grounded in the uploaded contract):

```json
{
  "answer": "Shartnomaning 5-moddasiga ko'ra, ijara oluvchi...",
  "session_id": "sess_proj_xyz",
  "message_id": "msg_ghi789",
  "latency_ms": 4230,
  "attachments": null
}
```

The answer references specific clauses from your uploaded document.

---

## Step 7: Ask about a specific section by attaching the file directly

If you want to include the full file text (not just the top-K matching chunks), pass `file_ids`:

```bash
curl -X POST http://localhost:8080/api/v3/chat/ask \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "tutorial-user-1",
    "session_id": "sess_proj_xyz",
    "query": "Shartnomaning 3-bo'\''limini qisqacha izohlang",
    "assistant": "contract_analyzer",
    "file_ids": ["file_def456"],
    "stream": false
  }'
```

`file_ids` causes the `load_file_and_project_context` node to load the full OCR text of the specified files (up to `FILE_CONTENT_TOKEN_LIMIT` = 50,000 characters).

---

## What you built

You now know how to:
- Create a project workspace with custom instructions.
- Upload and OCR a document into Milvus.
- Chat with the document through the `project_id` and `file_ids` parameters.
- Understand the difference between project-scoped retrieval (Milvus `project_files`) and full-text attachment (`file_ids`).

**What's next:**
- [Reference: LangGraph State & Nodes](reference-langgraph-state.md) — see how `load_file_and_project_context` works
- [Docs: File Management](file-management.md) — full file management API

---

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| Upload fails with 500 | GCS credentials not configured or bucket doesn't exist |
| `status: "failed"` on file | Datalab OCR failed — check `DATALAB_API_KEY` and that the PDF isn't password-protected |
| Answer doesn't reference the contract | `project_id` not included in the chat request |
| Slow upload | Large PDF with many pages — OCR + embedding is proportional to page count |
| `FILE_CONTENT_TOKEN_LIMIT` error | Extracted text exceeds 50,000 characters; split the document into smaller files |
