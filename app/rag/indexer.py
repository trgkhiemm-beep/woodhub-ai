# app/rag/indexer.py
import json
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer

# Model đa ngữ hỗ trợ tiếng Việt tốt và nhẹ
model = SentenceTransformer('keepitreal/vietnamese-sbert')

def build_index():
    with open("data/documents.json", "r", encoding="utf-8") as f:
        docs = json.load(f)
    
    texts = [doc["content"] for doc in docs]
    print("Đang embedding...")
    embeddings = model.encode(texts)
    
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(np.array(embeddings).astype('float32'))
    
    faiss.write_index(index, "data/faiss_index.bin")
    print("Đã lưu index tại data/faiss_index.bin")

if __name__ == "__main__":
    build_index()