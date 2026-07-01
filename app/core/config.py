import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "WOODHUB AI"
    WORKSHOP_API_URL: str | None = os.getenv("WORKSHOP_API_URL")
    GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")

settings = Settings()