# app/rag/retriever.py
import os
import json
import faiss
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('keepitreal/vietnamese-sbert')

INDEX_PATH = "data/faiss_index.bin"
DOCS_PATH = "data/documents.json"

index = None
docs = []

if os.path.exists(INDEX_PATH) and os.path.exists(DOCS_PATH):
    index = faiss.read_index(INDEX_PATH)
    with open(DOCS_PATH, "r", encoding="utf-8") as f:
        docs = json.load(f)
else:
    print("CANH BAO: FAISS index hoac documents.json chua duoc khoi tao.")
    print("Chuc nang RAG se khong tra ve ket qua cho den khi ban chay scripts/sync_data.py va app/rag/indexer.py")

def search_products(query: str, top_k: int = 3):
    if index is None or not docs:
        return []
        
    query_vector = model.encode([query])
    distances, indices = index.search(query_vector.astype('float32'), top_k)
    
    results = []
    for i in indices[0]:
        if i != -1 and i < len(docs): # Không lấy giá trị empty hoặc out of bounds
            results.append(docs[i])
    return results