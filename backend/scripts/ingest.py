import os
from pathlib import Path
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
import chromadb

# Anchored to the project root instead of a bare relative path - the SAME fix
# already applied to database.py's DB_PATH and to app.py's chroma_db path, for
# the exact same reason: a bare "docs" / "./chroma_db" points to a DIFFERENT
# real folder depending on which directory you happen to run this script from.
# This file now lives in backend/scripts/, so the project root is three levels
# up from here (backend/scripts/ingest.py -> backend/scripts -> backend -> the
# project root). Anchoring it means this script always reads the same docs/
# folder and always builds the same chroma_db/ folder that app.py reads from,
# no matter what directory your terminal happens to be in when you run it.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DOCS_PATH = PROJECT_ROOT / "docs"
CHROMA_DB_PATH = PROJECT_ROOT / "chroma_db"

# Set up local embedding model via Ollama
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")

# 1. Load documents from the docs folder
print("📄 Loading documents...")
documents = SimpleDirectoryReader(str(DOCS_PATH)).load_data()
print(f"   Loaded {len(documents)} document(s)")

# 2. Set up chunking (200-500 tokens per chunk)
splitter = SentenceSplitter(chunk_size=512, chunk_overlap=50)

# 3. Set up ChromaDB (local vector database)
print("🗄️  Setting up ChromaDB...")
chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_PATH))
chroma_collection = chroma_client.get_or_create_collection("rag_collection")
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)

# 4. Embed chunks and store in ChromaDB
print("🔢 Embedding and indexing chunks...")
index = VectorStoreIndex.from_documents(
    documents,
    storage_context=storage_context,
    transformations=[splitter],
    show_progress=True
)

print(f"✅ Done! Your documents are indexed and stored in {CHROMA_DB_PATH}")