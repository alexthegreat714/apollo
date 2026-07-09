""" 
config.py - Apollo Configuration Manager

Loads configuration from .env file and provides typed access to all settings.
"""

import os
from typing import Iterable, List, Optional
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from Apollo root directory
APOLLO_ROOT = Path(__file__).parent
ENV_PATH = APOLLO_ROOT / ".env"
load_dotenv(ENV_PATH)

_DEFAULT_WATCHLIST = [
    # Semis / AI compute
    "NVDA", "AMD", "AVGO", "TSM", "QCOM", "MU", "AMAT", "LRCX", "ARM",
    # Mega-cap tech
    "MSFT", "AAPL", "AMZN", "GOOGL", "META",
    # Software / cloud
    "CRM", "NOW", "PANW", "SNOW", "SHOP",
    # Internet / streaming
    "MELI",
    # Financials
    "JPM", "GS", "BAC", "MS", "V", "MA", "AXP", "SCHW", "BLK",
    # Energy
    "XOM", "CVX", "SLB", "OXY",
    # Healthcare
    "LLY", "UNH", "JNJ", "ABBV", "MRK", "PFE", "TMO", "ISRG",
    # Industrials
    "CAT", "HON", "GE", "DE", "LMT",
    # Consumer
    "WMT", "COST", "HD", "NKE", "MCD",
]

HIGH_RISK_REVIEW_TICKERS = ["PLTR", "RTX"]


def get_watchlist(raw: Optional[str] = None) -> List[str]:
    """Return normalized, deduplicated tickers from APOLLO_WATCHLIST or defaults."""
    source = raw if raw is not None else os.getenv("APOLLO_WATCHLIST", "")
    if not source.strip():
        source = ",".join(_DEFAULT_WATCHLIST)
    parts: Iterable[str] = [part.strip().upper() for part in source.split(",")]
    result: List[str] = []
    seen = set()
    for ticker in parts:
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        result.append(ticker)
    return result or list(_DEFAULT_WATCHLIST)


DEFAULT_WATCHLIST = list(_DEFAULT_WATCHLIST)


def get_high_risk_review_tickers(raw: Optional[str] = None) -> List[str]:
    """Tickers held out of default standard eligibility after poor backtest behavior."""
    source = raw if raw is not None else os.getenv("APOLLO_HIGH_RISK_REVIEW_TICKERS", ",".join(HIGH_RISK_REVIEW_TICKERS))
    parts: Iterable[str] = [part.strip().upper() for part in source.split(",")]
    result: List[str] = []
    seen = set()
    for ticker in parts:
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        result.append(ticker)
    return result

_SECTOR_MAP: dict = {
    # Technology — semis / AI compute
    "NVDA": "tech", "AMD": "tech", "AVGO": "tech", "TSM": "tech",
    "QCOM": "tech", "MU": "tech", "AMAT": "tech", "LRCX": "tech", "ARM": "tech",
    # Technology — mega-cap / software / cloud
    "MSFT": "tech", "AAPL": "tech", "AMZN": "tech", "GOOGL": "tech", "META": "tech",
    "CRM": "tech", "NOW": "tech", "PANW": "tech", "PLTR": "tech", "SNOW": "tech",
    "UBER": "tech", "SHOP": "tech", "NFLX": "tech", "MELI": "tech",
    # Financials
    "JPM": "financials", "GS": "financials", "BAC": "financials", "MS": "financials",
    "V": "financials", "MA": "financials", "AXP": "financials", "SCHW": "financials",
    "BLK": "financials",
    # Energy
    "XOM": "energy", "CVX": "energy", "SLB": "energy", "OXY": "energy",
    # Healthcare
    "LLY": "healthcare", "UNH": "healthcare", "JNJ": "healthcare", "ABBV": "healthcare",
    "MRK": "healthcare", "PFE": "healthcare", "TMO": "healthcare", "ISRG": "healthcare",
    # Industrials
    "CAT": "industrials", "HON": "industrials", "GE": "industrials",
    "RTX": "industrials", "DE": "industrials", "LMT": "industrials",
    # Consumer
    "WMT": "consumer", "COST": "consumer", "HD": "consumer", "NKE": "consumer", "MCD": "consumer",
}


def get_sector(ticker: str) -> str:
    return _SECTOR_MAP.get(str(ticker or "").strip().upper(), "other")

class Config:
    """Apollo configuration loaded from .env file."""

    # Model Configuration
    MODEL_NAME: str = os.getenv("MODEL_NAME", "gemma4:31b")
    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "gemma4:31b")
    WATCHLIST: List[str] = get_watchlist(os.getenv("APOLLO_WATCHLIST"))

    # RAG Configuration
    RAG_DIR: str = os.getenv("RAG_DIR", "./chroma_db")
    RAG_COLLECTION: str = os.getenv("RAG_COLLECTION", "apollo_financial")
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))

    # Server Configuration
    PORT: int = int(os.getenv("PORT", "5010"))
    HOST: str = os.getenv("HOST", "127.0.0.1")
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
        print(f"WATCHLIST: {','.join(cls.WATCHLIST)}")
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
