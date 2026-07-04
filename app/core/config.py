import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "WOODHUB AI"
    WORKSHOP_API_URL: str | None = os.getenv("WORKSHOP_API_URL")
    GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
    
    # --- CÁC BIẾN CẤU HÌNH CHO OLLAMA ---
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    OLLAMA_TIMEOUT: float = float(os.getenv("OLLAMA_TIMEOUT", "60.0"))
    OLLAMA_NUM_PREDICT: int = int(os.getenv("OLLAMA_NUM_PREDICT", "150"))
    OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # --- CORS: QUẢN LÝ QUYỀN TRUY CẬP FRONTEND ---
    # Mặc định là "*" (cho phép tất cả các nguồn truy cập)
    cors_origins_list: list[str] = [
        origin.strip() 
        for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    ]

settings = Settings()