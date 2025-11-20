"""
corpus_expander.py - Corpus expansion with web scraping and OCR.

Enables Apollo to grow its own dataset from approved sources.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
import requests

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Tesseract path for Windows
TESSERACT_EXE = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Whitelisted domains for web scraping
WHITELISTED_DOMAINS = [
    "federalreserve.gov",
    "fred.stlouisfed.org",
    "bls.gov",
    "sec.gov",
    "treasury.gov",
    "cbo.gov",
    "bea.gov",
    "imf.org",
    "worldbank.org",
    "ecb.europa.eu",
    "bankofengland.co.uk",
]

# Log file for expansion activities
EXPANSION_LOG = Path(__file__).resolve().parents[1] / "logs" / "rag_expansion.log"


def _check_tesseract() -> bool:
    """Check if Tesseract is available."""
    if not os.path.exists(TESSERACT_EXE):
        logger.warning(f"OCR unavailable — Tesseract not found at {TESSERACT_EXE}")
        return False
    return True


def _log_expansion(action: str, details: Dict[str, Any]) -> None:
    """Log expansion activity."""
    EXPANSION_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "action": action,
        **details
    }
    try:
        with EXPANSION_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Failed to log expansion: {e}")


def _is_domain_whitelisted(url: str) -> bool:
    """Check if URL domain is in whitelist."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return any(allowed in domain for allowed in WHITELISTED_DOMAINS)
    except Exception:
        return False


def _check_robots_txt(url: str) -> bool:
    """
    Check robots.txt for permission to scrape.
    Returns True if allowed, False if blocked.
    """
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

        response = requests.get(robots_url, timeout=10)
        if response.status_code != 200:
            return True  # No robots.txt = allow

        # Simple check for Disallow: /
        content = response.text.lower()
        if "disallow: /" in content and "disallow: /?" not in content:
            # Check if it's a blanket disallow
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if "user-agent: *" in line:
                    # Check following lines for disallow
                    for j in range(i + 1, min(i + 10, len(lines))):
                        if lines[j].strip() == "disallow: /":
                            return False
                        if lines[j].startswith("user-agent:"):
                            break

        return True
    except Exception as e:
        logger.warning(f"Could not check robots.txt: {e}")
        return True  # Allow on error


class CorpusExpander:
    """
    Expands the RAG corpus from web sources and OCR.
    """

    def __init__(self, rag_dir: str = None, collection: str = None):
        """
        Initialize the corpus expander.

        Args:
            rag_dir: RAG directory
            collection: Collection name
        """
        self.rag_dir = rag_dir or os.getenv("RAG_DIR", "./chroma_db")
        self.collection = collection or os.getenv("RAG_COLLECTION", "apollo_financial")
        self.tesseract_available = _check_tesseract()

        # Rate limiting
        self._last_request_time = 0
        self._min_request_interval = 2.0  # seconds

    def gather_web(self, query: str, max_results: int = 5) -> List[Dict[str, Any]]:
        """
        Gather content from whitelisted web sources.

        Args:
            query: Search query
            max_results: Maximum results to return

        Returns:
            List of extracted content
        """
        results = []

        # Build URLs for whitelisted sources
        urls_to_check = self._build_source_urls(query)

        for url in urls_to_check[:max_results * 2]:
            # Domain check
            if not _is_domain_whitelisted(url):
                logger.warning(f"Skipping non-whitelisted domain: {url}")
                _log_expansion("skipped_domain", {"url": url, "reason": "not_whitelisted"})
                continue

            # Robots.txt check
            if not _check_robots_txt(url):
                logger.warning(f"Blocked by robots.txt: {url}")
                _log_expansion("skipped_robots", {"url": url})
                continue

            # Rate limiting
            self._rate_limit()

            # Fetch content
            try:
                content = self._fetch_and_extract(url)
                if content:
                    results.append({
                        "url": url,
                        "text": content,
                        "fetched_at": datetime.utcnow().isoformat() + "Z"
                    })
                    _log_expansion("fetched_web", {"url": url, "chars": len(content)})

                    if len(results) >= max_results:
                        break

            except Exception as e:
                logger.error(f"Failed to fetch {url}: {e}")
                _log_expansion("fetch_failed", {"url": url, "error": str(e)})

        return results

    def _build_source_urls(self, query: str) -> List[str]:
        """Build URLs for whitelisted sources based on query."""
        urls = []

        # FRED (Federal Reserve Economic Data)
        fred_query = query.replace(" ", "+")
        urls.append(f"https://fred.stlouisfed.org/searchresults/?st={fred_query}")

        # BLS (Bureau of Labor Statistics)
        urls.append(f"https://www.bls.gov/search/results.htm?cx=searchsite&q={fred_query}")

        # Federal Reserve
        urls.append(f"https://www.federalreserve.gov/searchResults.aspx?query={fred_query}")

        return urls

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _fetch_and_extract(self, url: str) -> Optional[str]:
        """
        Fetch URL and extract text content.

        Args:
            url: URL to fetch

        Returns:
            Extracted text or None
        """
        headers = {
            "User-Agent": "ApolloBot/1.0 (Financial Research; +https://github.com/apollo)"
        }

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()

        return self.extract_text_from_html(response.text)

    def extract_text_from_html(self, html: str) -> str:
        """
        Extract clean text from HTML.

        Args:
            html: HTML content

        Returns:
            Extracted text
        """
        # Remove scripts and styles
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', html)

        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()

        # Decode HTML entities
        import html as html_module
        text = html_module.unescape(text)

        return text

    def ocr_pdf(self, pdf_path: str) -> Optional[str]:
        """
        Extract text from PDF using OCR.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Extracted text or None
        """
        if not self.tesseract_available:
            logger.error("OCR unavailable — Tesseract not found")
            return None

        if not os.path.exists(pdf_path):
            logger.error(f"PDF not found: {pdf_path}")
            return None

        try:
            # Convert PDF to images using pdf2image or similar
            # For now, use a simplified approach with ghostscript if available

            extracted_text = []

            with tempfile.TemporaryDirectory() as tmpdir:
                # Try using pdftoppm if available (from poppler)
                output_prefix = os.path.join(tmpdir, "page")

                try:
                    # Convert PDF pages to images
                    subprocess.run(
                        ["pdftoppm", "-png", pdf_path, output_prefix],
                        check=True,
                        capture_output=True
                    )
                except FileNotFoundError:
                    logger.error("pdftoppm not found. Install poppler for PDF support.")
                    _log_expansion("ocr_failed", {"pdf": pdf_path, "reason": "pdftoppm_missing"})
                    return None

                # OCR each image
                for img_file in sorted(Path(tmpdir).glob("*.png")):
                    result = subprocess.run(
                        [TESSERACT_EXE, str(img_file), "stdout"],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        extracted_text.append(result.stdout)

            full_text = "\n\n".join(extracted_text)
            _log_expansion("ocr_success", {"pdf": pdf_path, "chars": len(full_text)})

            return full_text

        except Exception as e:
            logger.exception(f"OCR failed: {e}")
            _log_expansion("ocr_failed", {"pdf": pdf_path, "error": str(e)})
            return None

    def chunk_text(
        self,
        text: str,
        chunk_size: int = 512,
        overlap: int = 50
    ) -> List[str]:
        """
        Chunk text into smaller pieces.

        Args:
            text: Text to chunk
            chunk_size: Target chunk size in characters
            overlap: Overlap between chunks

        Returns:
            List of text chunks
        """
        if not text:
            return []

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size

            # Try to break at sentence boundary
            if end < len(text):
                # Look for sentence end
                for i in range(min(50, end - start)):
                    if end - i > start and text[end - i] in '.!?':
                        end = end - i + 1
                        break

            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)

            start = end - overlap

        return chunks

    def assign_kind_auto(self, text: str) -> str:
        """
        Automatically assign document kind based on content.

        Args:
            text: Document text

        Returns:
            Assigned kind
        """
        text_lower = text.lower()

        # Keyword-based classification
        if any(kw in text_lower for kw in ["gdp", "inflation", "unemployment", "cpi"]):
            return "macro"
        elif any(kw in text_lower for kw in ["sec filing", "10-k", "10-q", "regulation"]):
            return "regulation"
        elif any(kw in text_lower for kw in ["forecast", "projection", "outlook"]):
            return "projection"
        elif any(kw in text_lower for kw in ["earnings", "revenue", "profit", "quarterly"]):
            return "report"
        elif any(kw in text_lower for kw in ["how to", "guide", "learn", "basics"]):
            return "education"
        elif any(kw in text_lower for kw in ["market", "stock", "index", "trading"]):
            return "news"
        else:
            return "report"

    def expand_from_url(self, url: str) -> Dict[str, Any]:
        """
        Expand corpus from a single URL.

        Args:
            url: URL to ingest

        Returns:
            Expansion result
        """
        if not _is_domain_whitelisted(url):
            return {"error": f"Domain not whitelisted: {url}"}

        if not _check_robots_txt(url):
            return {"error": f"Blocked by robots.txt: {url}"}

        try:
            content = self._fetch_and_extract(url)
            if not content:
                return {"error": "No content extracted"}

            chunks = self.chunk_text(content)
            kind = self.assign_kind_auto(content)

            _log_expansion("expand_url", {
                "url": url,
                "chunks": len(chunks),
                "kind": kind
            })

            return {
                "success": True,
                "url": url,
                "chunks": len(chunks),
                "kind": kind,
                "sample": chunks[0][:200] if chunks else ""
            }

        except Exception as e:
            return {"error": str(e)}

    def expand_from_pdf(self, pdf_path: str) -> Dict[str, Any]:
        """
        Expand corpus from a PDF file using OCR.

        Args:
            pdf_path: Path to PDF

        Returns:
            Expansion result
        """
        text = self.ocr_pdf(pdf_path)
        if not text:
            return {"error": "OCR extraction failed"}

        chunks = self.chunk_text(text)
        kind = self.assign_kind_auto(text)

        _log_expansion("expand_pdf", {
            "pdf": pdf_path,
            "chunks": len(chunks),
            "kind": kind
        })

        return {
            "success": True,
            "pdf": pdf_path,
            "chunks": len(chunks),
            "kind": kind,
            "chars_extracted": len(text)
        }

    def expand_auto(self, topic: str) -> Dict[str, Any]:
        """
        Automatically expand corpus for a topic.

        Args:
            topic: Topic to expand (e.g., "macro", "investing")

        Returns:
            Expansion result
        """
        # Topic-specific queries
        topic_queries = {
            "macro": ["GDP growth forecast", "inflation rate current", "Federal Reserve policy"],
            "investing": ["portfolio diversification", "asset allocation strategy"],
            "risk": ["market volatility analysis", "risk management strategies"],
            "tax": ["capital gains tax rates", "tax-loss harvesting rules"],
        }

        queries = topic_queries.get(topic, [f"{topic} financial analysis"])

        results = []
        for query in queries:
            web_results = self.gather_web(query, max_results=2)
            for item in web_results:
                chunks = self.chunk_text(item["text"])
                results.append({
                    "url": item["url"],
                    "chunks": len(chunks)
                })

        _log_expansion("expand_auto", {
            "topic": topic,
            "sources_processed": len(results)
        })

        return {
            "success": True,
            "topic": topic,
            "sources_processed": results,
            "total_sources": len(results)
        }


# Global expander instance
_expander = None


def get_expander() -> CorpusExpander:
    """Get or create the global corpus expander."""
    global _expander
    if _expander is None:
        _expander = CorpusExpander()
    return _expander
