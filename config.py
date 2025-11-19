"""
config.py - Apollo Configuration Manager

Loads configuration from .env file and provides typed access to all settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from Apollo root directory
APOLLO_ROOT = Path(__file__).parent
ENV_PATH = APOLLO_ROOT / ".env"
load_dotenv(ENV_PATH)


class Config:
    """Apollo configuration loaded from .env file."""

    # Model Configuration
    MODEL_NAME: str = os.getenv("MODEL_NAME", "Fino1-8B.Q6_K")
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "Fino1-8B.Q6_K")

    # RAG Configuration
    RAG_DIR: str = os.getenv("RAG_DIR", "./chroma_db")
    RAG_COLLECTION: str = os.getenv("RAG_COLLECTION", "apollo_financial")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))

    # Server Configuration
    PORT: int = int(os.getenv("PORT", "5100"))
    HOST: str = os.getenv("HOST", "0.0.0.0")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # Logging Configuration
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Deep Mode Configuration
    DEEP_MODE_ENABLED: bool = os.getenv("DEEP_MODE_ENABLED", "true").lower() == "true"
    DEEP_MODE_MAX_STEPS: int = int(os.getenv("DEEP_MODE_MAX_STEPS", "5"))
    DEEP_MODE_TIMEOUT: int = int(os.getenv("DEEP_MODE_TIMEOUT", "120"))

    # Tool Configuration
    TOOLS_ENABLED: bool = os.getenv("TOOLS_ENABLED", "true").lower() == "true"
    TAX_YEAR: int = int(os.getenv("TAX_YEAR", "2024"))
    DEFAULT_INFLATION_RATE: float = float(os.getenv("DEFAULT_INFLATION_RATE", "0.03"))

    # Ingestion Configuration
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "512"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))
    MAX_FILE_SIZE_MB: int = int(os.getenv("MAX_FILE_SIZE_MB", "50"))

    @classmethod
    def get_rag_path(cls) -> Path:
        """Get absolute path to RAG directory."""
        rag_path = Path(cls.RAG_DIR)
        if not rag_path.is_absolute():
            rag_path = APOLLO_ROOT / rag_path
        return rag_path

    @classmethod
    def get_log_path(cls) -> Path:
        """Get absolute path to log directory."""
        log_path = Path(cls.LOG_DIR)
        if not log_path.is_absolute():
            log_path = APOLLO_ROOT / log_path
        return log_path

    @classmethod
    def print_config(cls):
        """Print current configuration."""
        print("=" * 60)
        print("Apollo Configuration")
        print("=" * 60)
        print(f"MODEL_NAME: {cls.MODEL_NAME}")
        print(f"OLLAMA_HOST: {cls.OLLAMA_HOST}")
        print(f"EMBEDDING_MODEL: {cls.EMBEDDING_MODEL}")
        print(f"RAG_DIR: {cls.RAG_DIR}")
        print(f"RAG_COLLECTION: {cls.RAG_COLLECTION}")
        print(f"RAG_TOP_K: {cls.RAG_TOP_K}")
        print(f"PORT: {cls.PORT}")
        print(f"HOST: {cls.HOST}")
        print(f"DEBUG: {cls.DEBUG}")
        print(f"LOG_DIR: {cls.LOG_DIR}")
        print(f"LOG_LEVEL: {cls.LOG_LEVEL}")
        print(f"DEEP_MODE_ENABLED: {cls.DEEP_MODE_ENABLED}")
        print(f"DEEP_MODE_MAX_STEPS: {cls.DEEP_MODE_MAX_STEPS}")
        print(f"TOOLS_ENABLED: {cls.TOOLS_ENABLED}")
        print(f"TAX_YEAR: {cls.TAX_YEAR}")
        print(f"CHUNK_SIZE: {cls.CHUNK_SIZE}")
        print("=" * 60)


# Singleton instance
config = Config()


if __name__ == "__main__":
    Config.print_config()
