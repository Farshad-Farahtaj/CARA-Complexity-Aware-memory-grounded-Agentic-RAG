import os
from llama_index.core import SimpleDirectoryReader, VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.ollama import OllamaEmbedding
import chromadb

# Set up local embedding model via Ollama
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")

# 1. Load documents from /data folder
print("📄 Loading documents...")
documents = SimpleDirectoryReader("docs").load_data()
print(f"   Loaded {len(documents)} document(s)")

# 2. Set up chunking (200-500 tokens per chunk)
splitter = SentenceSplitter(chunk_size=512, chunk_overlap=50)

# 3. Set up ChromaDB (local vector database)
print("🗄️  Setting up ChromaDB...")
chroma_client = chromadb.PersistentClient(path="./chroma_db")
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

print("✅ Done! Your documents are indexed and stored in ./chroma_db")