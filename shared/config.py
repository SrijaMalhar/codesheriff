import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "")
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
SHADOW_MODE: bool = os.getenv("SHADOW_MODE", "false").lower() == "true"
ANALYSIS_SERVICE_URL: str = os.getenv("ANALYSIS_SERVICE_URL", "http://localhost:8002")
COMMENT_SERVICE_URL: str = os.getenv("COMMENT_SERVICE_URL", "http://localhost:8003")
DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./codesheriff.db")
