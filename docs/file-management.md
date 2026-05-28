# System Architecture: Document Pipeline & Agentic Editing

## 1. Overview
This document outlines the architecture for the Document Processing Pipeline. **All operations are scoped within a Project.**

For **sharing projects with other users** (invite links, members, UI permissions), see [frontend-project-collaboration.md](frontend-project-collaboration.md).
The workflow follows this lifecycle:
1.  **Project Creation**: User creates a workspace/project to hold related documents.
2.  **File Ingestion**: Secure upload of files to a specific Project.
3.  **Chat Sessions**: Users create chat sessions within a Project to discuss documents or request edits (similar to ChatGPT history).
4.  **RAG (Retrieval-Augmented Generation)**: Q&A is strictly scoped to the Project's documents.
5.  **Agentic Editing**: AI-driven modification of Project files.

## 2. Architecture Diagram

```mermaid
graph TD
    User[User / Client] -->|1. Create Project| API[FastAPI Backend]
    User -->|2. Upload File to Project| API
    User -->|3. Create Chat Session| API
    User -->|4. Chat / Edit (Session Context)| API

    subgraph Storage Layer
        GCS[Google Cloud Storage]
        Mongo[(MongoDB Metadata)]
        Milvus[(Milvus Vector DB)]
    end

    subgraph Processing Layer
        Worker[Background Worker / Celery]
        Parser[LlamaParse / Unstructured]
    end

    subgraph Agentic Layer
        Orchestrator[LangChain Agent]
        Editor[Doc Editor Tool]
        LLM[LLM (DeepSeek/Omni)]
    end

    %% Flows
    API -->|Create Project Record| Mongo
    API -->|Save Raw File| GCS
    API -->|Link File to Project| Mongo
    API -->|Trigger Processing| Worker

    Worker -->|Read File| GCS
    Worker -->|Extract Text| Parser
    Worker -->|Update Status| Mongo
    Worker -->|Store Embeddings (Partition: ProjectID)| Milvus
    
    API -->|Create Session Record| Mongo
    API -->|Log Message (SessionID)| Mongo
    API -->|Route Request (SessionID + ProjectID)| Orchestrator

    Orchestrator -->|Retrieve Context (ProjectID)| Milvus
    Orchestrator -->|Generate Answer| LLM
    
    Orchestrator -->|Edit Request| Editor
    Editor -->|Read Template/File| GCS
    Editor -->|Generate New Version| Editor
    Editor -->|Save New Version| GCS
    Editor -->|Link New Version to Project| Mongo
```

## 3. Detailed Components

### 3.1 API Layer (FastAPI)
-   **Main Endpoints**:
    -   `POST /projects`: Create a new project workspace.
    -   `POST /projects/{project_id}/upload/files`: Upload files to a specific project.
    -   `GET /projects/{project_id}/files`: List files in a specific project.
    -   `DELETE /projects/{project_id}/files/{file_id}`: Delete a specific file from a project.
    -   `POST /projects/{project_id}/sessions`: Create a new chat session.
    -   `GET /projects/{project_id}/sessions`: List all chat sessions in a project.
    -   `GET /projects/{project_id}/sessions/{session_id}`: Get chat history for a session.
    -   `POST /projects/{project_id}/sessions/{session_id}/chat`: Send a message to a specific session (RAG/Edit).
    -   `POST /projects/{project_id}/sessions/{session_id}/{message_id}`: Save a message to a specific session.
    -   `DELETE /projects/{project_id}/sessions/{session_id}/{message_id}`: Delete a specific message from a session.
-   **Responsibilities**: Request validation, auth, enforcing project access control.

### 3.2 Storage Layer
-   **Google Cloud Storage (GCS)**:
    -   **Structure**: `projects/{project_id}/files/{file_id}/{filename}` (Updated for Project Isolation).
    -   **Versioning**: "Edits" create new file objects (e.g., `contract_v2.docx`).
-   **MongoDB**:
    -   **Collection**: `projects`
        ```json
        {
            "_id": "project_uuid",
            "user_id": "user_id",
            "name": "Legal Contracts",
            "created_at": "..."
        }
        ```
    -   **Collection**: `chat_sessions`
        ```json
        {
            "_id": "session_uuid",
            "project_id": "project_uuid",
            "user_id": "user_id",
            "title": "Contract Review",
            "created_at": "...",
            "updated_at": "..."
        }
        ```
    -   **Collection**: `messages`
        ```json
        {
            "_id": "msg_uuid",
            "session_id": "session_uuid",
            "role": "user" | "assistant",
            "content": "...",
            "created_at": "..."
        }
        ```
    -   **Collection**: `files`
        ```json
        {
            "_id": "uuid",
            "project_id": "project_uuid", 
            "filename": "agreement.docx",
            "gcs_path": "...",
            "status": "indexed", // uploading, processing, ready, error
            "version": 1,
            "parent_file_id": null
        }
        ```
-   **Milvus**:
    -   Stores vector embeddings.
    -   **Partitioning/Filtering**: Queries MUST filter by `project_id` to ensure users only retrieve from their own relevant documents.

### 3.3 Processing & Ingestion
-   **Trigger**: Async task started after successful upload.
-   **Tools**:
    -   **PDFs**: `LlamaParse` or `Unstructured` for high-fidelity extraction.
    -   **DOCX**: `python-docx`.
-   **Output**: Chunks text -> Embeds -> Inserts into Milvus with `project_id` metadata field.

### 3.4 Agentic Editing
-   **Concept**: The agent operates within the Project context.
-   **Workflow**:
    1.  Agent identifies intent: "Edit the contract in this project".
    2.  Agent reads document text/structure from the Project's file list.
    3.  Agent modifies content.
    4.  Agent saves new file to GCS under the same Project.
    5.  Agent records new file metadata in MongoDB linked to the Project.

## 4. Recommended Frameworks

| Component | Recommendation | Why? |
| :--- | :--- | :--- |
| **Orchestration** | **LangChain** | Existing stack integration, mature agent tooling. |
| **Doc Processing** | **`python-docx`** | Robust API for reading/writing `.docx` XML structure. |
| **PDF Parsing** | **`LlamaParse`** | Best-in-class for preserving layout/tables in complex PDFs. |
| **Async Tasks** | **Celery + Redis** | Reliable queue for handling heavy ingestion jobs. |