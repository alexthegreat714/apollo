"""
rag_writer.py - ChromaDB Writer for Apollo RAG

Writes documents and embeddings to ChromaDB using PersistentClient.
Uses the same embedding function and client as apollo_rag.retriever for consistency.
"""

import chromadb
import os
import uuid
from typing import List, Dict, Any, Optional
from pathlib import Path

# Import from retriever to ensure consistency with the RAG system
from apollo_rag.retriever import _get_embedding_function, _get_chroma_client


class RAGWriter:
    """
    Writes documents to ChromaDB for RAG retrieval.
    Uses the same embedding function and client as the retriever.
    """

    def __init__(
        self,
        persist_dir: str = None,
        collection_name: str = None
    ):
        """
        Initialize the RAG writer.

        Args:
            persist_dir: Directory for ChromaDB persistence
            collection_name: Name of the collection
        """
        self.persist_dir = persist_dir or os.getenv("RAG_DIR", "./chroma_db")
        self.collection_name = collection_name or os.getenv("RAG_COLLECTION", "apollo_financial")

        # Ensure directory exists
        Path(self.persist_dir).mkdir(parents=True, exist_ok=True)

        # Use the same ChromaDB client as retriever (singleton pattern)
        self.client = _get_chroma_client(self.persist_dir)

        # Get the embedding function from retriever (ensures consistency)
        embedding_fn = _get_embedding_function()

        # Get or create collection with the same embedding function as retriever
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )

    def write(
        self,
        documents: List[str],
        metadatas: List[Dict[str, Any]] = None,
        ids: List[str] = None,
        embeddings: List[List[float]] = None
    ) -> Dict[str, Any]:
        """
        Write documents to the collection.

        Args:
            documents: List of document texts
            metadatas: List of metadata dicts (one per document)
            ids: List of document IDs (generated if not provided)
            embeddings: Pre-computed embeddings (ChromaDB will generate if not provided)

        Returns:
            Result dictionary with count and IDs
        """
        if not documents:
            return {"count": 0, "ids": []}

        # Generate IDs if not provided
        if not ids:
            ids = [f"doc_{uuid.uuid4().hex[:12]}" for _ in documents]

        # Ensure metadatas exist
        if not metadatas:
            metadatas = [{} for _ in documents]

        # Flatten metadata (ChromaDB requires flat dict)
        flat_metadatas = []
        for meta in metadatas:
            flat_meta = self._flatten_metadata(meta)
            flat_metadatas.append(flat_meta)

        try:
            if embeddings:
                self.collection.add(
                    documents=documents,
                    metadatas=flat_metadatas,
                    ids=ids,
                    embeddings=embeddings
                )
            else:
                self.collection.add(
                    documents=documents,
                    metadatas=flat_metadatas,
                    ids=ids
                )

            return {
                "count": len(documents),
                "ids": ids,
                "collection": self.collection_name
            }

        except Exception as e:
            return {
                "count": 0,
                "ids": [],
                "error": str(e)
            }

    def write_chunks(
        self,
        chunks: List[Dict[str, Any]],
        base_id: str = None,
        embeddings: List[List[float]] = None
    ) -> Dict[str, Any]:
        """
        Write chunked documents to the collection.

        Args:
            chunks: List of chunk dictionaries from chunker
            base_id: Base ID for the document (chunks get suffixed)
            embeddings: Pre-computed embeddings

        Returns:
            Result dictionary
        """
        if not chunks:
            return {"count": 0, "ids": []}

        base_id = base_id or f"doc_{uuid.uuid4().hex[:8]}"

        documents = []
        metadatas = []
        ids = []

        for i, chunk in enumerate(chunks):
            documents.append(chunk["text"])
            metadatas.append(chunk.get("metadata", {}))
            ids.append(f"{base_id}_chunk_{i:03d}")

        return self.write(documents, metadatas, ids, embeddings)

    def query(
        self,
        query_text: str,
        n_results: int = 5,
        where: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Query the collection.

        Args:
            query_text: Query text
            n_results: Number of results to return
            where: Metadata filter

        Returns:
            Query results
        """
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where=where,
                include=["documents", "metadatas", "distances"]
            )
            return results
        except Exception as e:
            return {"error": str(e)}

    def count(self) -> int:
        """Get document count in collection."""
        return self.collection.count()

    def delete_collection(self) -> bool:
        """Delete the entire collection."""
        try:
            self.client.delete_collection(self.collection_name)
            return True
        except Exception:
            return False

    def _flatten_metadata(self, meta: Dict[str, Any]) -> Dict[str, Any]:
        """
        Flatten nested metadata for ChromaDB.

        ChromaDB requires flat metadata with string/int/float/bool values.
        """
        flat = {}
        for key, value in meta.items():
            if isinstance(value, dict):
                # Flatten nested dict
                for nested_key, nested_value in value.items():
                    if isinstance(nested_value, (str, int, float, bool)):
                        flat[f"{key}_{nested_key}"] = nested_value
                    elif isinstance(nested_value, list):
                        flat[f"{key}_{nested_key}"] = ",".join(str(v) for v in nested_value)
            elif isinstance(value, list):
                flat[key] = ",".join(str(v) for v in value)
            elif isinstance(value, (str, int, float, bool)):
                flat[key] = value
            # Skip other types
        return flat


if __name__ == "__main__":
    print("Testing Apollo RAG Writer")
    print("=" * 60)

    # Use test directory
    writer = RAGWriter(
        persist_dir="./test_chroma_db",
        collection_name="test_financial"
    )

    print(f"Persist dir: {writer.persist_dir}")
    print(f"Collection: {writer.collection_name}")
    print(f"Initial count: {writer.count()}")

    # Test write
    test_docs = [
        "The Federal Reserve raised interest rates by 25 basis points.",
        "Stock market indices reached new highs in Q4 2024.",
        "Inflation remained at 3.2% according to the latest CPI report."
    ]

    test_metas = [
        {"kind": "news", "source": "Fed", "date": "2024-12-01"},
        {"kind": "news", "source": "Market", "date": "2024-12-10"},
        {"kind": "report", "source": "BLS", "date": "2024-12-15"}
    ]

    result = writer.write(test_docs, test_metas)
    print(f"\nWrite result: {result}")
    print(f"Count after write: {writer.count()}")

    # Test query
    query_result = writer.query("interest rates", n_results=2)
    print(f"\nQuery results for 'interest rates':")
    if "documents" in query_result:
        for i, doc in enumerate(query_result["documents"][0]):
            print(f"  {i+1}. {doc[:60]}...")

    # Cleanup
    writer.delete_collection()
    print("\nTest collection deleted")
