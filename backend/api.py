# ── How to run ────────────────────────────────────────────────
# From the PROJECT ROOT (1_RAG/), not from inside backend/:
#
#   pip install fastapi uvicorn python-multipart
#   uvicorn backend.api:app --reload
#   then open http://localhost:8000
# ─────────────────────────────────────────────────────────────

import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# ── Resolve all paths relative to project root ────────────────
ROOT        = Path(__file__).parent.parent
DOCS_DIR    = ROOT / "docs"
CHROMA_PATH = ROOT / "chroma_db"
BACKUP_DIR  = ROOT / "chatBackup"
STATIC_DIR  = ROOT / "frontend" / "static"

# Load .env from project root FIRST
load_dotenv(ROOT / ".env")
# Ensure required directories exist
for d in [DOCS_DIR, BACKUP_DIR, CHROMA_PATH]:
    d.mkdir(exist_ok=True)
    os.chmod(d, 0o755)

from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import List

from llama_index.core import VectorStoreIndex, StorageContext, Settings, SimpleDirectoryReader
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.embeddings.ollama import OllamaEmbedding
import chromadb

COLLECTION         = "rag_collection"
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx", ".md"}
Settings.llm        = GoogleGenAI(model="gemma-4-31b-it")
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")

# ── Single persistent ChromaDB client — never killed ──────────
chroma_client_global = chromadb.PersistentClient(path=str(CHROMA_PATH))

# ── Helper: build / rebuild the index ─────────────────────────
def build_index():
    """Delete & recreate the collection, then reindex all docs in DOCS_DIR."""
    global chroma_client_global

    # Wipe existing collection data cleanly (no file system operations)
    try:
        chroma_client_global.delete_collection(COLLECTION)
    except Exception:
        pass  # didn't exist yet — fine

    chroma_collection = chroma_client_global.get_or_create_collection(COLLECTION)
    vector_store      = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context   = StorageContext.from_defaults(vector_store=vector_store)
    splitter          = SentenceSplitter(chunk_size=512, chunk_overlap=50)

    documents = SimpleDirectoryReader(str(DOCS_DIR)).load_data()
    index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
        transformations=[splitter],
    )
    return index.as_query_engine(similarity_top_k=3)

# ── Load index at startup (only if real docs exist) ───────────
query_engine = None
real_docs    = [
    f for f in DOCS_DIR.iterdir()
    if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS
] if DOCS_DIR.exists() else []

if real_docs:
    print(f"🗄️  Found {len(real_docs)} doc(s) — loading index at startup...")
    try:
        query_engine = build_index()
        print("✅ Index loaded. API ready.")
    except Exception as e:
        print(f"⚠️  Could not load index at startup: {e}")
else:
    print("📂 No docs found in docs/. Waiting for upload...")

# ── Chat backup ───────────────────────────────────────────────
session_start = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
SESSION_FILE  = BACKUP_DIR / f"chat_{session_start}.json"

def save_chat(history: list):
    payload = {
        "saved_at":   datetime.now().strftime("%Y-%m-%d_%H-%M-%S"),
        "session_id": session_start,
        "messages":   history,
    }
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

# ── FastAPI app ───────────────────────────────────────────────
app = FastAPI(title="Clinic Assistant API")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

class ChatRequest(BaseModel):
    message:     str
    history:     list = []
    new_session: bool = False

class ChatResponse(BaseModel):
    answer: str

# ── Routes ────────────────────────────────────────────────────
@app.get("/")
async def serve_index():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/status")
async def status():
    return {"ready": query_engine is not None}

@app.post("/upload")
async def upload_documents(files: List[UploadFile] = File(...)):
    global query_engine, session_start, SESSION_FILE

    if not files or all(f.filename == "" for f in files):
        return JSONResponse(status_code=400, content={"error": "No files received."})

    saved = []
    for file in files:
        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            continue
        dest = DOCS_DIR / file.filename
        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)
        saved.append(file.filename)

    if not saved:
        return JSONResponse(
            status_code=400,
            content={"error": "No valid files (.txt, .pdf, .docx, .md) were uploaded."}
        )

    try:
        # Rebuild index — deletes & recreates collection without touching the folder
        query_engine = build_index()

        session_start = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        SESSION_FILE  = BACKUP_DIR / f"chat_{session_start}.json"

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Ingestion failed: {str(e)}"}
        )

    return {
        "success":  True,
        "ingested": saved,
        "count":    len(saved),
        "message":  f"✅ {len(saved)} document(s) ingested successfully. You can now ask questions.",
    }

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    global SESSION_FILE, session_start

    if query_engine is None:
        return ChatResponse(
            answer="⚠️ No documents have been ingested yet. Please upload documents first."
        )

    if req.new_session:
        session_start = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        SESSION_FILE  = BACKUP_DIR / f"chat_{session_start}.json"

    try:
        response = query_engine.query(req.message)
        answer   = str(response)
    except Exception as e:
        answer = f"⚠️ Something went wrong: {str(e)}"

    updated = list(req.history) + [
        {"role": "user",      "content": req.message},
        {"role": "assistant", "content": answer},
    ]
    save_chat(updated)
    return ChatResponse(answer=answer)