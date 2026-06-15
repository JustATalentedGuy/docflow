# Docflow

Multi-agent document processing and query API. Upload any PDF or image; a LangGraph pipeline classifies, extracts text (OCR if scanned), captions images, chunks, embeds, and indexes it. Query in natural language; hybrid search (BM25 + vector + RRF) retrieves context, and Groq LLaMA-3.1 generates grounded answers. Every agent step is traced in LangSmith.

For the AWS deployment plan and zero-cost demo setup, see [CLOUD-SETUP-GUIDE.md](CLOUD-SETUP-GUIDE.md).

---

## Table of Contents

1. [Interview-ready technical documentation](#interview-ready-technical-documentation)
1. [What you'll see running](#1-what-youll-see-running)
2. [Prerequisites](#2-prerequisites)
3. [Get your free API keys (10 minutes)](#3-get-your-free-api-keys-10-minutes)
4. [Local setup — everything in Docker](#4-local-setup--everything-in-docker)
5. [Verify it's working](#5-verify-its-working)
6. [Upload and query a document](#6-upload-and-query-a-document)
7. [Run the tests](#7-run-the-tests)
8. [Demo recording walkthrough](#8-demo-recording-walkthrough)
9. [AWS Free Tier deployment (EC2)](#9-aws-free-tier-deployment-ec2)
10. [Troubleshooting](#10-troubleshooting)
11. [Architecture at a glance](#11-architecture-at-a-glance)

---

## Interview-ready technical documentation

### Problem statement and solution

Docflow solves the problem of asking grounded questions over personal or academic documents, especially lecture PDFs, scanned handouts, and slide-style material. A user should be able to upload files, ask natural-language questions, continue a conversation, and receive answers based only on that user's own uploaded documents.

The solution is a full-stack retrieval-augmented generation (RAG) system. It provides login-based user isolation, asynchronous document processing, text cleanup for presentation-style PDFs, hybrid retrieval over indexed chunks, chat history, and grounded LLM responses with source snippets. Uploads, file metadata, vector search, chat threads, and message history are all scoped to the authenticated user.

### High-level architecture and modules

| Module | Purpose |
|---|---|
| React frontend | Login/register, upload files, manage files, create chats, continue chat history, and ask document questions. |
| FastAPI backend | Exposes authentication, upload, file management, chat, query, and health endpoints. |
| Agent pipeline | Classifies documents, extracts text, cleans slide artifacts, captions images, chunks text, embeds chunks, and indexes them. |
| Celery worker | Runs expensive document processing outside the request/response path. |
| Redis | Serves as Celery broker and short-lived job status store. |
| SQLite / PostgreSQL | SQLite stores metadata locally; the AWS deployment uses a PostgreSQL container on EC2. |
| MinIO/S3 | Stores raw uploaded files separately from application metadata. |
| Qdrant | Stores parent/child chunks, vectors, and metadata payloads for user-scoped retrieval. |
| Hybrid search layer | Combines BM25 lexical retrieval and vector similarity search, then fuses rankings. |
| Nginx + Docker Compose | Provides local deployment, reverse proxying, and service orchestration. |

### Algorithms and techniques used

- **RAG pipeline:** retrieves relevant document context before asking the LLM to answer.
- **Document classification:** separates born-digital PDFs, scanned PDFs, and images so each can follow the right extraction path.
- **OCR:** uses Tesseract for scanned PDF text extraction.
- **Text cleaning:** removes repeated slide headers, footers, page counters, duplicate lines, and presentation chrome before indexing.
- **Parent/child chunking:** child chunks are embedded for precise retrieval; parent chunks are passed to the LLM for richer context.
- **Embeddings:** sentence-transformer vectors are normalized for cosine similarity search.
- **Hybrid retrieval:** combines Qdrant vector search with BM25 lexical search to handle both semantic queries and exact technical terms.
- **Reciprocal Rank Fusion (RRF):** merges vector and BM25 rankings without requiring score normalization.
- **Conversation-aware querying:** recent chat history is used to resolve follow-up references such as "it", while retrieved document chunks remain the source of truth.

### API documentation

All endpoints under `/api/v1` that access user data require a bearer token, except registration and login.

| Group | Endpoint | Purpose | Input | Output |
|---|---|---|---|---|
| Auth | `POST /api/v1/auth/register` | Create user and session | `email`, `password` | token, expiry, user |
| Auth | `POST /api/v1/auth/login` | Start a session | `email`, `password` | token, expiry, user |
| Auth | `GET /api/v1/auth/me` | Fetch current user | bearer token | user profile |
| Auth | `POST /api/v1/auth/logout` | Delete user sessions | bearer token | `{ "ok": true }` |
| Files | `POST /api/v1/upload` | Upload a PDF/image for processing | multipart `file` | `job_id`, `file_id`, status |
| Files | `GET /api/v1/status/{job_id}` | Poll processing status | user-owned `job_id` | queued/processing/completed/failed |
| Files | `GET /api/v1/files` | List current user's files | bearer token | file metadata list |
| Files | `DELETE /api/v1/files/{file_id}` | Delete file from DB, object storage, and index | user-owned `file_id` | `{ "ok": true }` |
| Chats | `POST /api/v1/chats` | Create a chat thread | title | chat metadata |
| Chats | `GET /api/v1/chats` | List user's chats | bearer token | chat list |
| Chats | `GET /api/v1/chats/{chat_id}` | Load chat with messages | user-owned `chat_id` | chat detail |
| Chats | `POST /api/v1/chats/{chat_id}/messages` | Ask a chat-aware question | query, optional `file_id`, `top_k` | answer, sources, model |
| Chats | `DELETE /api/v1/chats/{chat_id}` | Delete a chat and messages | user-owned `chat_id` | `{ "ok": true }` |
| Query | `POST /api/v1/query` | One-off document question | query, optional `file_id`/`job_id`/`chat_id`, `top_k` | answer, sources, model |
| Ops | `GET /health` | Liveness check | none | status and version |

The key API invariant is user scoping: file lists, job status, deletion, chat loading, chat messages, and retrieval filters all use the authenticated user's ID.

### Database and data organization

SQLite stores relational application metadata locally. The AWS deployment uses the same schema in a PostgreSQL container on the EC2 instance:

| Table | Purpose |
|---|---|
| `users` | Registered users and password hashes. |
| `sessions` | Bearer tokens, user ownership, creation time, and expiry. |
| `files` | Uploaded file metadata, processing status, S3 key, size, content type, and owner user ID. |
| `chats` | User-owned chat threads with title and timestamps. |
| `messages` | User and assistant messages linked to a chat and user. |

Raw uploaded files are stored in MinIO/S3 under user/job-specific keys, which keeps large binary objects out of SQLite. Qdrant stores indexed chunks with payload metadata such as `user_id`, `file_id`, `job_id`, filename, chunk type, and parent-child relationship.

User isolation is enforced in more than one place. SQL ownership filters prevent users from listing or mutating another user's files or chats. Qdrant payload filters prevent retrieval from crossing user boundaries, even when multiple users have documents in the same vector collection.

### End-to-end user workflow

1. **Register or log in:** the backend creates or validates a user, stores a session in SQLite, and returns a bearer token. The React UI stores the token and sends it with later requests.
2. **Upload a document:** FastAPI validates the file type and size, uploads the raw bytes to MinIO/S3, creates a `files` row, writes initial job status, and queues a Celery task.
3. **Process the document:** the worker classifies the document, extracts text or OCR output, cleans slide-style artifacts, captions images when needed, creates parent/child chunks, embeds child chunks, and upserts all chunks into Qdrant with user/file metadata.
4. **Check status:** the UI polls the status endpoint or refreshes the file list. Redis gives fast job status, while SQLite stores durable file status.
5. **Create or continue a chat:** the backend creates a chat row or loads prior messages for the selected user-owned chat.
6. **Ask a question:** the query service builds a retrieval query using the current question and recent chat history, filters retrieval to the current user and optional file, runs hybrid search, sends retrieved context to Groq, stores the user/assistant messages, and returns the answer with sources.
7. **Delete a file:** the backend verifies ownership, deletes Qdrant points for that `user_id` and `file_id`, deletes the raw object from MinIO/S3, and removes the metadata row.

### System design reasoning, tradeoffs, and limitations

| Choice | Reasoning | Tradeoffs and limitations |
|---|---|---|
| FastAPI | Fast to build, type-friendly, and gives OpenAPI docs automatically. | Auth/session handling is custom and would need hardening for production. |
| Celery + Redis | Keeps uploads responsive while heavy OCR/embedding work runs asynchronously. | Adds operational complexity and requires worker monitoring. |
| SQLite | Simple, portable, and excellent for a local/demo system. | Should be replaced with Postgres for high concurrency, migrations, and production durability. |
| MinIO/S3 | Separates large raw files from relational metadata and mirrors cloud deployment patterns. | Adds another service and requires object/index consistency on deletion. |
| Qdrant | Purpose-built vector database with metadata payload filters. | Requires careful payload design and index cleanup when files are deleted. |
| BM25 + vector search | BM25 handles exact technical terms; vectors handle semantic similarity. | The BM25 cache is in memory and needs a more robust refresh strategy at larger scale. |
| Parent/child chunks | Improves retrieval precision while still giving the LLM enough context. | More complex indexing and storage than single-level chunks. |
| Groq LLM | Fast hosted generation and simple LangChain integration. | External dependency with rate limits, latency variability, and possible cost. |
| React UI | Demonstrates the complete user workflow, not just API calls. | Adds frontend build/deployment surface and proxy concerns. |

Current production gaps are intentional for a portfolio/demo system: SQLite should become Postgres, bearer sessions should become a more complete auth strategy, old indexed files must be reprocessed after text-cleaning improvements, and BM25 cache management should be made distributed or persistent.

### Interview pitch summary

Docflow is a user-scoped RAG application for asking grounded questions over uploaded documents. A user logs in, uploads PDFs or images, and the backend processes them asynchronously through a document pipeline that extracts, cleans, chunks, embeds, and indexes the content. At query time, the system uses both semantic vector search and BM25 lexical search, fuses results with RRF, and sends only the retrieved user-owned context to the LLM. Chats are persisted, so follow-up questions can use conversation history while answers remain grounded in the user's documents. The system demonstrates practical full-stack design: async workers, object storage, vector search, relational metadata, user isolation, and a React interface, with clear tradeoffs for what would change in production.

---

## 1. What you'll see running

| Service | URL | What it is |
|---|---|---|
| **Docflow API + Swagger UI** | http://localhost/docs | FastAPI with all endpoints |
| **MinIO console** | http://localhost:9001 | Local S3 — browse uploaded files |
| **Qdrant dashboard** | http://localhost:6333/dashboard | Browse vector collections |
| **Redis** | localhost:6379 | Job status + Celery queue |

The API now exposes user-scoped auth, file, and chat endpoints:

```
POST   /api/v1/auth/register       -> create a user and return a bearer token
POST   /api/v1/auth/login          -> return a bearer token
GET    /api/v1/auth/me             -> current user profile
POST   /api/v1/upload              -> accepts a file for the logged-in user
GET    /api/v1/status/{job_id}     -> poll a user-owned upload until "completed"
GET    /api/v1/files               -> list the current user's files
DELETE /api/v1/files/{file_id}     -> delete a file from storage and the vector index
GET    /api/v1/chats               -> list the current user's chats
POST   /api/v1/chats               -> create a chat
GET    /api/v1/chats/{chat_id}     -> get chat history
POST   /api/v1/chats/{chat_id}/messages -> ask a chat-aware question
POST   /api/v1/query               -> one-off natural language question
```

All upload, retrieval, deletion, and chat history operations are scoped to the authenticated user.
Chat queries include recent messages so follow-up questions can refer to earlier turns.

---

## 2. Prerequisites

| Tool | Version | Check |
|---|---|---|
| Docker Desktop | 4.x+ | `docker --version` |
| Docker Compose v2 | included with Docker Desktop | `docker compose version` |
| 8 GB RAM available to Docker | — | Docker Desktop → Settings → Resources |
| Python 3.11+ (for running tests locally) | optional | `python --version` |

**Disk space:** The Docker image is ~3 GB because PaddleOCR and sentence-transformers model weights are baked in. First build takes 15–25 minutes. Subsequent builds use the cache and take under 2 minutes.

---

## 3. Get your free API keys (10 minutes)

You need three keys. All are free with no credit card.

### 3.1 Groq API key (LLM inference)

1. Go to https://console.groq.com
2. Sign up with Google or GitHub
3. Click **API Keys** in the left sidebar → **Create API Key**
4. Copy the key starting with `gsk_`

### 3.2 Hugging Face token (BLIP-2 image captioning)

1. Go to https://huggingface.co/join
2. Sign up (free)
3. Go to https://huggingface.co/settings/tokens
4. Click **New token** → Type: **Read** → Create
5. Copy the token starting with `hf_`

### 3.3 LangSmith API key (agent tracing)

1. Go to https://smith.langchain.com
2. Sign up with Google or GitHub
3. Go to **Settings** → **API Keys** → **Create API Key**
4. Copy the key starting with `ls__`

---

## 4. Local setup — everything in Docker

### Step 1: Clone the repo

```bash
git clone https://github.com/yourusername/docflow.git
cd docflow
```

### Step 2: Create your `.env` file

```bash
cp .env.example .env
```

Open `.env` in any editor and set the three keys you collected above:

```bash
# The only lines you need to change:
GROQ_API_KEY=gsk_your_actual_key_here
HF_API_TOKEN=hf_your_actual_token_here
LANGCHAIN_API_KEY=ls__your_actual_key_here
```

Every other value in `.env` is pre-configured for the local Docker Compose stack and works as-is.

### Step 3: Build and start

```bash
docker compose -f infra/docker-compose.yml up --build -d
```

The first build downloads ~3 GB of model weights and takes **15–25 minutes**. You will see output like:

```
[+] Building 0.0s (2/2) FINISHED
 => [api] Pulling from library/python
 => [api] sentence-transformers model ready
 => [api] PaddleOCR models ready
```

Subsequent starts (after the image is built) take about **30 seconds**.

### Step 4: Watch the logs to confirm all services started

```bash
docker compose -f infra/docker-compose.yml logs -f api worker
```

Wait for both of these lines before testing:

```
{"event": "docflow_ready", ...}           # from api service
{"event": "task_started", ...}            # appears when first task runs
```

Press `Ctrl+C` to stop following logs (containers keep running).

---

## 5. Verify it's working

```bash
# Should return {"status": "ok", "version": "1.0.0"}
curl http://localhost/health
```

Open the Swagger UI at **http://localhost/docs** — you should see three endpoints listed.

---

## 6. Upload and query a document

### Option A: Swagger UI (best for demos)

1. Open http://localhost/docs
2. Click **POST /api/v1/upload** → **Try it out**
3. Click **Choose File**, select any PDF (lecture notes, a paper, anything)
4. Click **Execute** → copy the `job_id` from the response
5. Click **GET /api/v1/status/{job_id}** → **Try it out** → paste the job_id
6. Click **Execute** repeatedly until `status` is `"completed"` (30s–5min depending on file size)
7. Click **POST /api/v1/query** → **Try it out**
8. Enter body: `{"query": "What are the main topics covered?", "top_k": 5}`
9. Click **Execute** → see the answer + sources

### Option B: curl

```bash
# 1. Upload a file
JOB=$(curl -s -X POST http://localhost/api/v1/upload \
  -F "file=@/path/to/your/document.pdf" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

echo "Job ID: $JOB"

# 2. Poll status (run this repeatedly until you see "completed")
curl -s http://localhost/api/v1/status/$JOB | python3 -m json.tool

# 3. Query
curl -s -X POST http://localhost/api/v1/query \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"Summarise the key points\", \"top_k\": 5}" \
  | python3 -m json.tool
```

### Option C: Seed a whole folder

```bash
# Install requests if not already available
pip install requests

# Upload every PDF/image in a folder and wait for all to complete
python scripts/seed_index.py --folder ./my_documents --api http://localhost:80
```

---

## 7. Run the tests

Tests that don't require external services can run locally without Docker:

```bash
# Install test dependencies
pip install -r requirements.txt

# Run all unit tests (no Docker needed)
pytest tests/ -v

# Individual test files
pytest tests/test_hybrid_search.py -v    # RRF, BM25 — pure Python
pytest tests/test_agents.py -v           # chunker, embedder, classifier
pytest tests/test_api.py -v              # API endpoints (mocked services)
```

Expected output (all should pass):

```
tests/test_api.py::test_health_returns_ok                           PASSED
tests/test_api.py::test_upload_rejects_unsupported_extension        PASSED
tests/test_api.py::test_upload_accepts_pdf                          PASSED
tests/test_agents.py::test_chunker_produces_parent_and_child_chunks PASSED
tests/test_agents.py::test_embedder_output_shape                    PASSED
tests/test_hybrid_search.py::test_rrf_document_in_both_lists_ranks_first PASSED
... (20+ tests)
```

---

## 8. Demo recording walkthrough

This is the sequence that shows every major component in one screen recording. Aim for 3–5 minutes.

### Scene 1: Show the architecture (30 seconds)

Open the documentation PDF or architecture diagram. Briefly describe:
- "Upload goes to FastAPI, stored in MinIO (or S3 in production)"
- "LangGraph pipeline: classify → OCR if needed → caption images → chunk → embed → index"
- "Query uses hybrid BM25 + vector search, fused with RRF, then Groq LLaMA generates the answer"

### Scene 2: Upload a document (1 minute)

1. Open **http://localhost/docs** in the browser
2. Show the Swagger UI briefly
3. Upload a PDF (use a lecture note or a research paper you have)
4. Copy the `job_id`

### Scene 3: Show the file in MinIO (30 seconds)

1. Open **http://localhost:9001** (login: minioadmin / minioadmin)
2. Navigate to the `docflow-uploads` bucket
3. Show the uploaded file under `uploads/{job_id}/`

This proves the file was stored, not just accepted.

### Scene 4: Show the agent trace in LangSmith (1 minute)

1. Open **https://smith.langchain.com** → your `docflow` project
2. Click on the most recent trace
3. Show the node-by-node breakdown: classify → caption → chunk → embed → index
4. Click on the "chunk" node to show input/output
5. Show the timing for each node

This is the proof of LangGraph working — most projects don't have this.

### Scene 5: Poll status and query (1 minute)

1. Poll status until `"completed"`
2. Go to **POST /api/v1/query** in Swagger
3. Ask a specific question about the document you uploaded (not generic — pick something that proves it actually read the content)
4. Show the answer and the `sources` array with real chunk text and filenames

### Scene 6: Show the Qdrant collection (30 seconds)

1. Open **http://localhost:6333/dashboard**
2. Navigate to `docflow_chunks` collection
3. Show the point count and a sample payload
4. Run a quick search from the UI to show child chunks have vectors

### What makes this demo stand out

- The LangSmith trace with individual node timing is something most students don't have
- Showing MinIO proves you understand object storage, not just in-memory demos
- Querying with a specific, non-trivial question (not "what is this document about?") proves the retrieval actually works
- Mentioning "parent-child chunking" and "RRF fusion" during narration signals depth

---

## 9. AWS Free Tier deployment (EC2)

### 9.1 Launch EC2 instance

1. Go to AWS Console → EC2 → Launch Instance
2. Choose: **Amazon Linux 2023**, **t2.micro** (Free Tier eligible)
3. Storage: **20 GB gp3** (default 8 GB is not enough)
4. Security group: inbound **TCP 80** from `0.0.0.0/0`, **TCP 22** from your IP only
5. Launch with a key pair you have

### 9.2 Create S3 bucket

```bash
# From your local machine (needs AWS CLI configured)
aws s3 mb s3://docflow-uploads-yourname --region ap-south-1
```

Or create it in the AWS Console: S3 → Create bucket → name it → create.

### 9.3 SSH in and install Docker

```bash
ssh -i your-key.pem ec2-user@<EC2_PUBLIC_IP>

# Install Docker
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
newgrp docker

# Install Docker Compose plugin
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -SL \
  https://github.com/docker/compose/releases/download/v2.27.0/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

### 9.4 Deploy

```bash
git clone https://github.com/yourusername/docflow.git
cd docflow
cp .env.example .env
nano .env
```

In `.env`, make these changes for production:

```bash
# Remove this line (use real AWS S3):
# AWS_ENDPOINT_URL=http://minio:9000

# Set real AWS credentials:
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=your_secret...
AWS_REGION=ap-south-1
S3_BUCKET_NAME=docflow-uploads-yourname

# Set your API keys:
GROQ_API_KEY=gsk_...
HF_API_TOKEN=hf_...
LANGCHAIN_API_KEY=ls__...
```

Also edit `infra/docker-compose.yml` and remove the `minio` and `minio-init` services (they're not needed when using real S3).

```bash
# Build and run (takes 15-25 min on t2.micro)
docker compose -f infra/docker-compose.yml up --build -d

# Check it's up
curl http://localhost/health
```

Your API is now at `http://<EC2_PUBLIC_IP>/docs`.

### 9.5 IAM best practice

Instead of putting AWS keys in `.env`, attach an IAM role to the EC2 instance:

1. IAM → Roles → Create Role → EC2
2. Attach inline policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:PutObject", "s3:GetObject"],
    "Resource": "arn:aws:s3:::docflow-uploads-yourname/*"
  }]
}
```
3. EC2 → Actions → Security → Modify IAM role → attach this role
4. Remove `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` from `.env`

---

## 10. Troubleshooting

### Build fails at PaddleOCR download

```bash
# PaddleOCR sometimes times out downloading models. Retry:
docker compose -f infra/docker-compose.yml build --no-cache worker
```

If it consistently fails, add to Dockerfile before the paddleocr download line:
```dockerfile
RUN pip install --no-cache-dir paddlepaddle==2.6.1 && \
    python -m paddle.utils.run_check
```

### API returns 503 on /query immediately after startup

The BM25 index is empty because no documents have been indexed yet. Upload at least one document and wait for status `"completed"` before querying.

### Worker logs show `failed:ocr: ...` for a PDF

The PDF is scanned but PaddleOCR failed. Try:
- Confirm the PDF is a valid file (not corrupted)
- Check worker logs: `docker compose -f infra/docker-compose.yml logs worker`
- For very large scanned PDFs (>20 pages), try a smaller file first

### `bm25:dirty` key persists in Redis

If a worker crashes mid-task, the rebuild may not trigger. Force it:
```bash
# Connect to running API container and trigger a manual rebuild
docker compose -f infra/docker-compose.yml exec api python -c "
from app.storage.qdrant import load_all_child_chunks
from app.search.hybrid import searcher
texts, ids = load_all_child_chunks()
searcher.build_bm25_index(texts, ids)
print(f'BM25 rebuilt with {len(texts)} chunks')
"
```

### t2.micro runs out of memory during OCR

Reduce Celery concurrency in `docker-compose.yml`:
```yaml
command: >
  celery -A app.workers.tasks.celery_app worker
  --concurrency=1   # change from 2 to 1
```

### Qdrant collection already exists error on restart

This is handled gracefully — `init_collection()` checks first and skips creation if it exists. If you want a clean slate:
```bash
docker compose -f infra/docker-compose.yml down -v   # -v removes volumes
docker compose -f infra/docker-compose.yml up -d
```

### LangSmith traces not appearing

1. Confirm `LANGCHAIN_TRACING_V2=true` (not `True`, not `1`) in `.env`
2. Confirm `LANGCHAIN_API_KEY` is set correctly
3. Restart the api service: `docker compose -f infra/docker-compose.yml restart api`

---

## 11. Architecture at a glance

```
Client (curl / Swagger / seed_index.py)
        │
        ▼
   Nginx :80  ──────────────────────────────────────────────────────
        │                                                           │
        ▼                                                           ▼
 FastAPI (api)                                              [static assets]
   POST /upload                                                      
        │  1. validate + read bytes                                  
        │  2. upload_to_s3  ──────────→  MinIO / AWS S3             
        │  3. redis.setex("job:ID", "queued")                       
        │  4. process_document.delay()  ──→  Redis queue            
        │  5. return job_id (HTTP 202)                               
        │                                                           
   GET /status/{id}  ←── redis.get("job:ID")                       
   POST /query                                                       
        │  1. HybridSearcher.search()                               
        │        ├── Qdrant vector search (child chunks)            
        │        ├── BM25 search (in-memory)                        
        │        └── RRF fusion → parent chunk IDs                  
        │        └── retrieve_by_ids() ──→  Qdrant                 
        │  2. Groq LLaMA-3.1 (langchain-groq)                       
        │  3. return {answer, sources}                               
        │                                                           
Celery worker (worker)                                               
   process_document task                                             
        │                                                           
        ▼                                                           
   LangGraph PIPELINE                                               
        ├── classify  ──→  S3 download + fitz                       
        ├── ocr       ──→  S3 download + PaddleOCR      (scanned)   
        ├── caption   ──→  S3 download + HF Inference API           
        ├── chunk     ──→  RecursiveCharacterTextSplitter            
        ├── embed     ──→  sentence-transformers                    
        └── index     ──→  Qdrant upsert + redis "bm25:dirty"       
        │                                                           
        └── BM25 rebuild ──→  HybridSearcher.build_bm25_index()    

Storage layer:
   MinIO / S3    ← raw files
   Qdrant        ← chunk embeddings + payloads
   Redis         ← job status + Celery broker

Observability:
   structlog  → JSON to stdout → CloudWatch Logs Insights
   LangSmith  → agent traces → https://smith.langchain.com
```

---

## Stopping and cleaning up

```bash
# Stop all containers (data preserved in volumes)
docker compose -f infra/docker-compose.yml down

# Stop and DELETE all data (volumes removed)
docker compose -f infra/docker-compose.yml down -v

# Remove the built image to force a full rebuild
docker rmi docflow-api docflow-worker
```
