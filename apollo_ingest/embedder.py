"""
embedder.py - Document Embedding for Apollo RAG

Generates embeddings using Ollama's embedding API or model generate endpoint.
"""

import requests
from typing import List, Optional
import os


class DocumentEmbedder:
    """
    Generates embeddings for text chunks using Ollama.
    """

    def __init__(
        self,
        model: str = None,
        ollama_host: str = None
    ):
        """
        Initialize the embedder.

        Args:
            model: Model name for embeddings (defaults to env or Fino1-8B.Q6_K)
            ollama_host: Ollama API host (defaults to env or localhost:11434)
        """
        self.model = model or os.getenv("EMBEDDING_MODEL", os.getenv("OLLAMA_MODEL", "Fino1-8B.Q6_K"))
        self.ollama_host = ollama_host or os.getenv("OLLAMA_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434"))

    def embed(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            List of embedding floats, or None on error
        """
        if not text or not text.strip():
            return None

        try:
            # Try Ollama embeddings endpoint first
            response = requests.post(
                f"{self.ollama_host}/api/embeddings",
                json={
                    "model": self.model,
                    "prompt": text
                },
                timeout=60
            )
            response.raise_for_status()
            result = response.json()
            return result.get("embedding")

        except requests.exceptions.RequestException as e:
            print(f"Embedding error: {e}")
            return None

    def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embeddings (None for failed embeddings)
        """
        embeddings = []
        for text in texts:
            embedding = self.embed(text)
            embeddings.append(embedding)
        return embeddings

    def test_connection(self) -> bool:
        """
        Test connection to Ollama and model availability.

        Returns:
            True if connection and model work
        """
        try:
            # Test with simple text
            embedding = self.embed("test")
            return embedding is not None and len(embedding) > 0
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False


if __name__ == "__main__":
    print("Testing Apollo Document Embedder")
    print("=" * 60)

    embedder = DocumentEmbedder()
    print(f"Model: {embedder.model}")
    print(f"Host: {embedder.ollama_host}")

    print("\nTesting connection...")
    if embedder.test_connection():
        print("Connection: SUCCESS")

        test_text = "The Federal Reserve raised interest rates by 25 basis points."
        embedding = embedder.embed(test_text)

        if embedding:
            print(f"\nEmbedding generated successfully")
            print(f"Dimensions: {len(embedding)}")
            print(f"First 5 values: {embedding[:5]}")
        else:
            print("Failed to generate embedding")
    else:
        print("Connection: FAILED")
        print("Make sure Ollama is running and the model is available")
