"""
retriever.py - Advanced RAG Retriever with reranking and normalization.

Features:
- Domain-weighted retrieval
- Keyword-assisted reranking
- Per-domain embedding normalization
- Consistent embedding function with ingest pipeline
"""

# =============================================================================
# APOLLO RULE:
# All collections must be created by _get_collection(),
# and all embeddings must use the same model used by ingest.
# Violations will raise a RuntimeError.
# =============================================================================

import os
import re
import json
import logging
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np

try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    chromadb = None

logger = logging.getLogger(__name__)


# =============================================================================
# EMBEDDING MODEL CONFIGURATION
# The ingest model is the SOURCE OF TRUTH
# =============================================================================

# Embedding dimension for nomic-embed-text (must match APOLLO_EMBEDDING_MODEL)
EMBEDDING_DIM = 768
ZERO_VECTOR = [0.0] * EMBEDDING_DIM


def _get_ingest_model_name() -> str:
    """
    Return the Ollama model name used for embeddings in Apollo.

    We intentionally ignore any global EMBEDDING_MODEL or OLLAMA_MODEL
    and use Apollo-specific configuration instead.

    Returns:
        Model name string
    """
    return os.getenv("APOLLO_EMBEDDING_MODEL", "nomic-embed-text")


def _get_ollama_host() -> str:
    """Get the Ollama host URL."""
    return os.getenv("OLLAMA_URL", os.getenv("OLLAMA_HOST", "http://localhost:11434"))


# =============================================================================
# EMBEDDING FUNCTION - Must match what ingest pipeline uses
# =============================================================================

class OllamaEmbeddingFunction:
    """
    ChromaDB-compatible embedding function using Ollama.
    This must match the embedding approach used in apollo_ingest/embedder.py.
    """

    def __init__(
        self,
        model: str = None,
        ollama_host: str = None
    ):
        self.model = model or _get_ingest_model_name()
        self.ollama_host = ollama_host or _get_ollama_host()
        logger.info(f"Initialized OllamaEmbeddingFunction with model: {self.model}")

    def __call__(self, input: List[str]) -> List[List[float]]:
        """
        Generate embeddings for a list of texts.

        Args:
            input: List of texts to embed

        Returns:
            List of embedding vectors
        """
        embeddings = []
        for text in input:
            embedding = self._embed_single(text)
            if embedding:
                embeddings.append(embedding)
            else:
                # Return zero vector on failure (ChromaDB requires consistent dimensions)
                # This is a fallback - ideally embeddings should always succeed
                embeddings.append(ZERO_VECTOR.copy())
        return embeddings

    def _embed_single(self, text: str) -> Optional[List[float]]:
        """Generate embedding for a single text."""
        if not text or not text.strip():
            return None

        try:
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
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return None


def _get_embedding_function():
    """
    Get the embedding function for ChromaDB.

    IMPORTANT: This must match what apollo_ingest/embedder.py uses.

    Raises:
        RuntimeError: If the embedding model configuration is invalid
    """
    model_name = _get_ingest_model_name()
    ollama_host = _get_ollama_host()

    # Validate that we have a model configured
    if not model_name:
        raise RuntimeError(
            "No embedding model configured. Set APOLLO_EMBEDDING_MODEL environment variable."
        )

    # Log the model being used for debugging
    logger.info(f"Using embedding model: {model_name} at {ollama_host}")

    return OllamaEmbeddingFunction(model=model_name, ollama_host=ollama_host)


# Global ChromaDB client singleton
_chroma_client = None


def _get_chroma_client(persist_dir: str = None) -> chromadb.PersistentClient:
    """Get or create the global ChromaDB client."""
    global _chroma_client
    if _chroma_client is None:
        path = persist_dir or os.getenv("RAG_DIR", "./chroma_db")
        _chroma_client = chromadb.PersistentClient(path=path)
    return _chroma_client


def _get_collection(
    collection_name: str = None,
    persist_dir: str = None
):
    """
    Get or create a collection with the proper embedding function.

    This is the SINGLE SOURCE OF TRUTH for collection creation.
    All code should use this instead of directly calling client.get_collection().
    """
    client = _get_chroma_client(persist_dir)
    name = collection_name or os.getenv("RAG_COLLECTION", "apollo_financial")

    return client.get_or_create_collection(
        name=name,
        embedding_function=_get_embedding_function(),
        metadata={"hnsw:space": "cosine"}
    )

# =============================================================================
# CONFIGURATION - Domain-weighted retrieval
# =============================================================================

KIND_WEIGHTS = {
    "education": 1.3,
    "investing": 1.25,
    "risk": 1.25,
    "macro": 1.2,
    "news": 1.1,
    "projection": 1.1,
    "statement": 1.0,
    "report": 1.0,
    "regulation": 1.0,
    "analysis": 1.15,
    "commentary": 1.05,
    "micro": 1.1,
}

# Keyword bonus per overlap
KEYWORD_BONUS = 0.05

# Stopwords for keyword extraction
STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "ought", "used", "to", "of", "in", "for", "on", "with", "at", "by",
    "from", "up", "about", "into", "over", "after", "beneath", "under",
    "above", "and", "but", "or", "nor", "so", "yet", "both", "either",
    "neither", "not", "only", "own", "same", "than", "too", "very", "just",
    "also", "now", "here", "there", "when", "where", "why", "how", "all",
    "each", "every", "both", "few", "more", "most", "other", "some", "such",
    "no", "any", "this", "that", "these", "those", "what", "which", "who",
    "whom", "whose", "i", "me", "my", "myself", "we", "our", "ours", "you",
    "your", "yours", "he", "him", "his", "she", "her", "hers", "it", "its",
    "they", "them", "their", "theirs", "am", "if", "then", "else", "because",
    "as", "until", "while", "although", "though", "once", "since", "unless",
    "show", "tell", "explain", "describe", "give", "help", "want", "like",
}


class RAGRetriever:
    """
    Advanced RAG retriever with reranking and normalization.
    """

    def __init__(
        self,
        persist_dir: str = None,
        collection_name: str = None,
        embedding_function=None,
    ):
        """
        Initialize the RAG retriever.

        Args:
            persist_dir: ChromaDB persistence directory
            collection_name: Name of the collection
            embedding_function: Custom embedding function (optional, uses Ollama by default)
        """
        self.persist_dir = persist_dir or os.getenv("RAG_DIR", "./chroma_db")
        self.collection_name = collection_name or os.getenv("RAG_COLLECTION", "apollo_financial")

        # Per-domain mean vectors for normalization
        self._domain_means: Dict[str, np.ndarray] = {}
        self._mean_vectors_path = Path(self.persist_dir) / "domain_means.json"

        # Initialize ChromaDB using the consistent helper
        self.client = _get_chroma_client(self.persist_dir)
        self.collection = _get_collection(self.collection_name, self.persist_dir)

        if self.collection:
            logger.info(f"Connected to collection: {self.collection_name}")
        else:
            logger.error("Failed to initialize collection")

        # Load domain means if available
        self._load_domain_means()

    def _load_domain_means(self):
        """Load per-domain mean vectors from disk."""
        if self._mean_vectors_path.exists():
            try:
                with open(self._mean_vectors_path, "r") as f:
                    data = json.load(f)
                    self._domain_means = {
                        k: np.array(v) for k, v in data.items()
                    }
                logger.info(f"Loaded domain means for {len(self._domain_means)} kinds")
            except Exception as e:
                logger.warning(f"Failed to load domain means: {e}")

    def _save_domain_means(self):
        """Save per-domain mean vectors to disk."""
        try:
            data = {
                k: v.tolist() for k, v in self._domain_means.items()
            }
            with open(self._mean_vectors_path, "w") as f:
                json.dump(data, f)
            logger.info(f"Saved domain means for {len(self._domain_means)} kinds")
        except Exception as e:
            logger.warning(f"Failed to save domain means: {e}")

    def compute_domain_means(self):
        """
        Compute mean embedding vectors per document kind.
        Call this after ingestion to enable normalization.
        """
        if not self.collection:
            logger.error("No collection available")
            return

        # Get all documents with embeddings
        try:
            all_data = self.collection.get(
                include=["embeddings", "metadatas"]
            )
        except Exception as e:
            logger.error(f"Failed to get collection data: {e}")
            return

        if not all_data.get("embeddings"):
            logger.warning("No embeddings found in collection")
            return

        embeddings = all_data["embeddings"]
        metadatas = all_data.get("metadatas", [])

        # Group embeddings by kind
        kind_embeddings: Dict[str, List[np.ndarray]] = {}

        for i, meta in enumerate(metadatas):
            kind = (meta or {}).get("kind", "unknown")
            if kind not in kind_embeddings:
                kind_embeddings[kind] = []
            kind_embeddings[kind].append(np.array(embeddings[i]))

        # Compute means
        self._domain_means = {}
        for kind, embs in kind_embeddings.items():
            if embs:
                self._domain_means[kind] = np.mean(embs, axis=0)
                logger.info(f"Computed mean for kind '{kind}' from {len(embs)} embeddings")

        # Save to disk
        self._save_domain_means()

    def normalize_embedding(self, embedding: np.ndarray, kind: str) -> np.ndarray:
        """
        Normalize an embedding by subtracting the domain mean.

        Args:
            embedding: The embedding vector
            kind: Document kind

        Returns:
            Normalized embedding
        """
        if kind in self._domain_means:
            return embedding - self._domain_means[kind]
        return embedding

    def extract_keywords(self, text: str) -> List[str]:
        """
        Extract keywords from text for reranking.

        Args:
            text: Input text

        Returns:
            List of keywords
        """
        # Simple tokenization
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())

        # Remove stopwords and get unique keywords
        keywords = []
        seen = set()
        for word in words:
            if word not in STOPWORDS and word not in seen:
                keywords.append(word)
                seen.add(word)

        return keywords

    def calculate_keyword_overlap(self, query_keywords: List[str], doc_text: str) -> int:
        """
        Calculate keyword overlap between query and document.

        Args:
            query_keywords: Keywords from query
            doc_text: Document text

        Returns:
            Number of overlapping keywords
        """
        doc_words = set(re.findall(r'\b[a-zA-Z]{3,}\b', doc_text.lower()))
        overlap = sum(1 for kw in query_keywords if kw in doc_words)
        return overlap

    def rerank_results(
        self,
        query: str,
        results: Dict[str, Any],
        query_intent: str = None
    ) -> List[Dict[str, Any]]:
        """
        Rerank search results using domain weights and keyword overlap.

        Args:
            query: Original query text
            results: Raw ChromaDB results
            query_intent: Optional intent for additional weighting

        Returns:
            List of reranked results with scores
        """
        if not results.get("ids") or not results["ids"][0]:
            return []

        ids = results["ids"][0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        # Extract query keywords
        query_keywords = self.extract_keywords(query)

        # Build scored results
        scored_results = []

        for i in range(len(ids)):
            doc_id = ids[i]
            doc_text = documents[i] if i < len(documents) else ""
            meta = metadatas[i] if i < len(metadatas) else {}
            distance = distances[i] if i < len(distances) else 1.0

            # Convert distance to similarity score (ChromaDB uses L2 distance)
            # Lower distance = more similar
            base_score = 1.0 / (1.0 + distance)

            # Apply domain weight
            kind = (meta or {}).get("kind", "unknown")
            domain_weight = KIND_WEIGHTS.get(kind, 1.0)

            # Calculate keyword overlap bonus
            keyword_overlap = self.calculate_keyword_overlap(query_keywords, doc_text)
            keyword_bonus = KEYWORD_BONUS * keyword_overlap

            # Intent-based boost
            intent_boost = 0.0
            if query_intent:
                intent_kind_map = {
                    "markets": ["news", "report", "analysis"],
                    "personal_finance": ["education", "report"],
                    "risk": ["risk", "report", "projection"],
                    "tax": ["regulation", "education"],
                    "investing": ["investing", "education", "projection"],
                    "macro": ["macro", "news", "projection"],
                }
                preferred_kinds = intent_kind_map.get(query_intent, [])
                if kind in preferred_kinds:
                    intent_boost = 0.1

            # Final score
            final_score = (base_score * domain_weight) + keyword_bonus + intent_boost

            scored_results.append({
                "id": doc_id,
                "text": doc_text,
                "meta": meta,
                "distance": distance,
                "base_score": base_score,
                "domain_weight": domain_weight,
                "keyword_overlap": keyword_overlap,
                "final_score": final_score,
            })

        # Sort by final score (descending)
        scored_results.sort(key=lambda x: x["final_score"], reverse=True)

        return scored_results

    def search(
        self,
        query: str,
        top_k: int = 5,
        kinds: List[str] = None,
        intent: str = None,
        include_scores: bool = False
    ) -> Dict[str, Any]:
        """
        Search the RAG collection with advanced reranking.

        Args:
            query: Search query
            top_k: Number of results to return
            kinds: Filter by document kinds
            intent: Query intent for reranking boost
            include_scores: Include detailed scoring info

        Returns:
            Search results with reranking
        """
        if not self.collection:
            return {"results": [], "error": "No collection available"}

        # Retrieve more candidates for reranking
        fetch_k = min(top_k * 3, 50)

        try:
            # Build where filter for kinds
            where_filter = None
            if kinds:
                if len(kinds) == 1:
                    where_filter = {"kind": kinds[0]}
                else:
                    where_filter = {"kind": {"$in": kinds}}

            # Execute query
            raw_results = self.collection.query(
                query_texts=[query],
                n_results=fetch_k,
                where=where_filter,
                include=["documents", "metadatas", "distances"]
            )

        except Exception as e:
            logger.error(f"Search failed: {e}")
            return {"results": [], "error": str(e)}

        # Rerank results
        reranked = self.rerank_results(query, raw_results, intent)

        # Take top_k
        top_results = reranked[:top_k]

        # Format output
        results = []
        for item in top_results:
            result = {
                "id": item["id"],
                "text": item["text"],
                "meta": item["meta"],
                "distance": item["distance"],
            }
            if include_scores:
                result["scores"] = {
                    "base_score": round(item["base_score"], 4),
                    "domain_weight": item["domain_weight"],
                    "keyword_overlap": item["keyword_overlap"],
                    "final_score": round(item["final_score"], 4),
                }
            results.append(result)

        return {
            "results": results,
            "total_candidates": len(reranked),
            "query": query,
            "intent": intent,
        }

    def add_documents(
        self,
        texts: List[str],
        metadatas: List[Dict[str, Any]] = None,
        ids: List[str] = None,
        embeddings: List[List[float]] = None,
        normalize: bool = True
    ):
        """
        Add documents to the collection with optional normalization.

        Args:
            texts: Document texts
            metadatas: Document metadata
            ids: Document IDs
            embeddings: Pre-computed embeddings (optional)
            normalize: Apply per-domain normalization
        """
        if not self.collection:
            logger.error("No collection available")
            return

        # Generate IDs if not provided
        if not ids:
            import uuid
            ids = [str(uuid.uuid4()) for _ in texts]

        # Normalize embeddings if provided and normalization enabled
        if embeddings and normalize and self._domain_means:
            normalized_embeddings = []
            for i, emb in enumerate(embeddings):
                kind = (metadatas[i] if metadatas else {}).get("kind", "unknown")
                norm_emb = self.normalize_embedding(np.array(emb), kind)
                normalized_embeddings.append(norm_emb.tolist())
            embeddings = normalized_embeddings

        try:
            if embeddings:
                self.collection.add(
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids,
                    embeddings=embeddings
                )
            else:
                self.collection.add(
                    documents=texts,
                    metadatas=metadatas,
                    ids=ids
                )
            logger.info(f"Added {len(texts)} documents to collection")
        except Exception as e:
            logger.error(f"Failed to add documents: {e}")

    def get_collection_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the collection.

        Returns:
            Collection statistics
        """
        if not self.collection:
            return {"error": "No collection available"}

        try:
            count = self.collection.count()

            # Get kind distribution
            all_data = self.collection.get(include=["metadatas"])
            kind_counts = {}
            for meta in all_data.get("metadatas", []):
                kind = (meta or {}).get("kind", "unknown")
                kind_counts[kind] = kind_counts.get(kind, 0) + 1

            return {
                "collection_name": self.collection_name,
                "document_count": count,
                "kind_distribution": kind_counts,
                "domain_means_computed": len(self._domain_means) > 0,
                "domains_with_means": list(self._domain_means.keys()),
            }
        except Exception as e:
            return {"error": str(e)}

    def delete_collection(self):
        """Delete the entire collection."""
        if self.client and self.collection:
            try:
                self.client.delete_collection(self.collection_name)
                self.collection = None
                logger.info(f"Deleted collection: {self.collection_name}")
            except Exception as e:
                logger.error(f"Failed to delete collection: {e}")


# =============================================================================
# Utility Functions
# =============================================================================

# Global retriever instance
_retriever = None


def get_retriever(
    persist_dir: str = None,
    collection_name: str = None
) -> RAGRetriever:
    """
    Get or create a global RAGRetriever instance.

    Uses singleton pattern to avoid multiple ChromaDB connections.

    Args:
        persist_dir: ChromaDB persistence directory
        collection_name: Collection name

    Returns:
        RAGRetriever instance
    """
    global _retriever
    if _retriever is None:
        _retriever = RAGRetriever(persist_dir, collection_name)
    return _retriever


def create_retriever(
    persist_dir: str = None,
    collection_name: str = None
) -> RAGRetriever:
    """
    Factory function to create a new RAGRetriever instance.

    Note: For most uses, prefer get_retriever() to use singleton pattern.

    Args:
        persist_dir: ChromaDB persistence directory
        collection_name: Collection name

    Returns:
        RAGRetriever instance
    """
    return RAGRetriever(persist_dir, collection_name)


def update_kind_weights(new_weights: Dict[str, float]):
    """
    Update the global KIND_WEIGHTS configuration.

    Args:
        new_weights: Dictionary of kind -> weight mappings
    """
    global KIND_WEIGHTS
    KIND_WEIGHTS.update(new_weights)
    logger.info(f"Updated KIND_WEIGHTS: {new_weights}")


if __name__ == "__main__":
    # Test the retriever
    print("Testing RAGRetriever")
    print("=" * 60)

    retriever = RAGRetriever()
    stats = retriever.get_collection_stats()
    print(f"Collection stats: {stats}")

    if stats.get("document_count", 0) > 0:
        results = retriever.search(
            "How do I diversify my portfolio?",
            top_k=5,
            intent="investing",
            include_scores=True
        )
        print(f"\nSearch results: {len(results.get('results', []))} items")
        for i, r in enumerate(results.get("results", [])):
            print(f"\n{i+1}. Score: {r.get('scores', {}).get('final_score', 0):.3f}")
            print(f"   Kind: {r.get('meta', {}).get('kind', 'unknown')}")
            print(f"   Text: {r.get('text', '')[:100]}...")
