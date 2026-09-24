import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "WOODHUB AI"
    
    # --- SUPABASE CONFIG ---
    SUPABASE_URL: str | None = os.getenv("SUPABASE_URL")
    SUPABASE_KEY: str | None = os.getenv("SUPABASE_KEY")

    # --- GROQ AI CONFIG ---
    GROQ_API_KEY: str | None = os.getenv("GROQ_API_KEY")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama3-8b-8192")
    
    # --- AWS BEDROCK CONFIG ---
    AWS_ACCESS_KEY_ID: str | None = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY: str | None = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_DEFAULT_REGION: str = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    BEDROCK_MODEL_ID: str = os.getenv("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")

    # --- WORKSHOP CONFIG ---
    WORKSHOP_API_URL: str | None = os.getenv("WORKSHOP_API_URL")
    
    # --- OLLAMA CONFIG ---
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    OLLAMA_TIMEOUT: float = float(os.getenv("OLLAMA_TIMEOUT", "60.0"))
    OLLAMA_NUM_PREDICT: int = int(os.getenv("OLLAMA_NUM_PREDICT", "150"))
    OLLAMA_TEMPERATURE: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    # --- CORS ---
    cors_origins_list: list[str] = [
        origin.strip() 
        for origin in os.getenv("CORS_ORIGINS", "*").split(",")
    ]

    # --- MESHY CONFIG ---
    MESHY_API_KEY: str | None = os.getenv("MESHY_API_KEY")
    MESHY_BASE_URL: str | None = os.getenv("MESHY_BASE_URL", "https://api.meshy.ai")

    # --- SYSTEM CONSTANTS FOR DATABASE-FIRST CHATBOT ---
    OUT_OF_SCOPE_MESSAGE: str = (
        "Xin lỗi, tôi chỉ hỗ trợ các câu hỏi liên quan "
        "đến nội thất gỗ và sản phẩm hiện có trong hệ thống."
    )

    UNKNOWN_MESSAGE: str = (
        "Tôi chưa hiểu rõ yêu cầu của bạn. "
        "Vui lòng mô tả cụ thể sản phẩm hoặc nhu cầu nội thất gỗ "
        "bạn đang quan tâm."
    )

    MISSING_PRODUCT_MESSAGE: str = (
        "Hệ thống hiện chưa có mặt hàng này."
    )

    ATTRIBUTE_NOT_FOUND_MESSAGE: str = (
        "Thông tin này hiện chưa có trong hệ thống."
    )

    DATABASE_ERROR_MESSAGE: str = (
        "Hiện tại hệ thống chưa thể kiểm tra thông tin sản phẩm. "
        "Vui lòng thử lại sau."
    )

    AMBIGUOUS_CLARIFICATION_MESSAGE: str = (
        "Tôi chưa xác định được sản phẩm bạn đang hỏi. "
        "Vui lòng gửi tên sản phẩm, mã sản phẩm hoặc thông tin sản phẩm cần kiểm tra."
    )

    # --- CONFIDENCE POLICY THRESHOLDS ---
    CONFIDENCE_IN_SCOPE_MIN: float = float(os.getenv("CONFIDENCE_IN_SCOPE_MIN", "0.85"))
    CONFIDENCE_AMBIGUOUS_MIN: float = float(os.getenv("CONFIDENCE_AMBIGUOUS_MIN", "0.50"))

settings = Settings()