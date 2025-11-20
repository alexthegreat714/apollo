"""
embedder.py - Document Embedding for Apollo RAG

Uses the same OllamaEmbeddingFunction as apollo_rag.retriever to ensure
consistent embedding dimensions between ingest and retrieval.
"""

from typing import List, Optional

# Import the embedding function from retriever to ensure consistency
from apollo_rag.retriever import OllamaEmbeddingFunction, _get_embedding_function


class DocumentEmbedder:
    """
    Generates embeddings for text chunks using the same Ollama embedding
    function as the RAG retriever.

    This ensures that ingest and retrieval use identical embedding models
    and dimensions.
    """

    def __init__(
        self,
        model: str = None,
        ollama_host: str = None
    ):
        """
        Initialize the embedder.

        Args:
            model: Model name for embeddings (ignored - uses retriever config)
            ollama_host: Ollama API host (ignored - uses retriever config)
        """
        # Use the same embedding function as the retriever
        # This ensures consistent dimensions between ingest and query
        self._embedding_fn = _get_embedding_function()
        self.model = self._embedding_fn.model
        self.ollama_host = self._embedding_fn.ollama_host

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

        result = self._embedding_fn([text])
        if result and result[0]:
            # Check for zero vector (indicates failure)
            if any(v != 0.0 for v in result[0]):
                return result[0]
        return None

    def embed_query(self, text: str) -> List[float]:
        """
        Generate embedding for a query text.

        Args:
            text: Query text to embed

        Returns:
            Embedding vector
        """
        result = self._embedding_fn([text])
        return result[0] if result else []

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple documents.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        return self._embedding_fn(texts)

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

        # Debug helper: print embedding length for a simple query
        print("\n" + "-" * 60)
        print("Debug: Embedding dimension check")
        vec = embedder.embed_query("test")
        print(f"Embedding length: {len(vec)}")
        print("-" * 60)
    else:
        print("Connection: FAILED")
        print("Make sure Ollama is running and the model is available")
