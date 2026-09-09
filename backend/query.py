from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.embeddings.ollama import OllamaEmbedding
from dotenv import load_dotenv
import chromadb

load_dotenv()

# Identify the LLM and embedding model to use
Settings.llm = GoogleGenAI(model="gemma-4-31b-it")
Settings.embed_model = OllamaEmbedding(model_name="nomic-embed-text")

# 1. Connect to the existing ChromaDB (already built in ingest.py)
print("🗄️  Connecting to ChromaDB...")
chroma_client = chromadb.PersistentClient(path="./chroma_db")
chroma_collection = chroma_client.get_or_create_collection("rag_collection")
vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)

# 2. Load the index from the vector store
index = VectorStoreIndex.from_vector_store(
    vector_store,
    storage_context=storage_context
)

# 3. Create the query engine
query_engine = index.as_query_engine(similarity_top_k=3)

# 4. Interactive loop — ask questions in the terminal
print("\n✅ RAG app ready! Ask anything about your clinic documents.")
print("   Type 'exit' to quit.\n")

while True:
    question = input("🙋 Your question: ").strip()
    if question.lower() in ["exit", "quit"]:
        print("👋 Goodbye!")
        break
    if not question:
        continue

    response = query_engine.query(question)
    print(f"\n🤖 Answer: {response}\n")
    print("-" * 60 + "\n")