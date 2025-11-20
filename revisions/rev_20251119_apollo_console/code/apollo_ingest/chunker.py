"""
chunker.py - Text Chunking for Apollo RAG

Provides configurable text chunking with overlap for optimal retrieval.
"""

import re
from typing import List, Dict, Any


class TextChunker:
    """
    Chunks text into segments for embedding and retrieval.

    Supports multiple chunking strategies:
    - fixed: Fixed size chunks with overlap
    - sentence: Sentence-based chunks
    - paragraph: Paragraph-based chunks
    """

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        strategy: str = "fixed"
    ):
        """
        Initialize the chunker.

        Args:
            chunk_size: Target size for each chunk (characters)
            chunk_overlap: Overlap between consecutive chunks
            strategy: Chunking strategy ('fixed', 'sentence', 'paragraph')
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.strategy = strategy

    def chunk(self, text: str, metadata: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Chunk text into segments.

        Args:
            text: Text to chunk
            metadata: Base metadata to include with each chunk

        Returns:
            List of chunk dictionaries with text and metadata
        """
        if not text or not text.strip():
            return []

        metadata = metadata or {}

        if self.strategy == "sentence":
            chunks = self._chunk_by_sentence(text)
        elif self.strategy == "paragraph":
            chunks = self._chunk_by_paragraph(text)
        else:
            chunks = self._chunk_fixed(text)

        # Add metadata and chunk info to each chunk
        result = []
        for i, chunk_text in enumerate(chunks):
            chunk_data = {
                "text": chunk_text,
                "chunk_index": i,
                "chunk_count": len(chunks),
                "char_start": text.find(chunk_text[:50]) if len(chunk_text) >= 50 else 0,
                "char_length": len(chunk_text),
                "metadata": {
                    **metadata,
                    "chunk_strategy": self.strategy,
                    "chunk_size_config": self.chunk_size,
                }
            }
            result.append(chunk_data)

        return result

    def _chunk_fixed(self, text: str) -> List[str]:
        """
        Chunk text into fixed-size segments.

        Args:
            text: Text to chunk

        Returns:
            List of text chunks
        """
        chunks = []
        for i in range(0, len(text), self.chunk_size):
            chunk = text[i:i + self.chunk_size].strip()
            if chunk:
                chunks.append(chunk)
        return chunks

    def _chunk_by_sentence(self, text: str) -> List[str]:
        """
        Chunk text by sentences, grouping to reach target size.

        Args:
            text: Text to chunk

        Returns:
            List of text chunks
        """
        # Split into sentences
        sentence_pattern = r'(?<=[.!?])\s+'
        sentences = re.split(sentence_pattern, text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return [text.strip()] if text.strip() else []

        chunks = []
        current_chunk = []
        current_length = 0

        for sentence in sentences:
            sentence_length = len(sentence)

            # If single sentence exceeds chunk size, add it anyway
            if sentence_length > self.chunk_size:
                if current_chunk:
                    chunks.append(' '.join(current_chunk))
                    current_chunk = []
                    current_length = 0
                chunks.append(sentence)
                continue

            # Check if adding this sentence exceeds chunk size
            if current_length + sentence_length + 1 > self.chunk_size:
                if current_chunk:
                    chunks.append(' '.join(current_chunk))
                current_chunk = [sentence]
                current_length = sentence_length
            else:
                current_chunk.append(sentence)
                current_length += sentence_length + 1

        # Add final chunk
        if current_chunk:
            chunks.append(' '.join(current_chunk))

        return chunks

    def _chunk_by_paragraph(self, text: str) -> List[str]:
        """
        Chunk text by paragraphs, grouping to reach target size.

        Args:
            text: Text to chunk

        Returns:
            List of text chunks
        """
        # Split into paragraphs
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        if not paragraphs:
            return [text.strip()] if text.strip() else []

        chunks = []
        current_chunk = []
        current_length = 0

        for para in paragraphs:
            para_length = len(para)

            # If single paragraph exceeds chunk size, use sentence chunking
            if para_length > self.chunk_size:
                if current_chunk:
                    chunks.append('\n\n'.join(current_chunk))
                    current_chunk = []
                    current_length = 0
                # Sub-chunk large paragraph by sentences
                sub_chunks = self._chunk_by_sentence(para)
                chunks.extend(sub_chunks)
                continue

            # Check if adding this paragraph exceeds chunk size
            if current_length + para_length + 2 > self.chunk_size:
                if current_chunk:
                    chunks.append('\n\n'.join(current_chunk))
                current_chunk = [para]
                current_length = para_length
            else:
                current_chunk.append(para)
                current_length += para_length + 2

        # Add final chunk
        if current_chunk:
            chunks.append('\n\n'.join(current_chunk))

        return chunks

    def estimate_chunks(self, text: str) -> int:
        """
        Estimate number of chunks without actually chunking.

        Args:
            text: Text to estimate

        Returns:
            Estimated number of chunks
        """
        if not text:
            return 0

        text_length = len(text)
        if text_length <= self.chunk_size:
            return 1

        effective_chunk_size = self.chunk_size - self.chunk_overlap
        return max(1, (text_length + effective_chunk_size - 1) // effective_chunk_size)


if __name__ == "__main__":
    # Test the chunker
    print("Testing Apollo Text Chunker")
    print("=" * 60)

    test_text = """
    The Federal Reserve's monetary policy decisions significantly impact financial markets.
    Interest rate changes affect borrowing costs for consumers and businesses alike.
    Higher rates typically slow economic growth but help control inflation.

    Stock markets often react negatively to rate hikes in the short term.
    However, moderate rate increases can signal a healthy economy.
    Investors should consider both immediate and long-term effects.

    Bond yields move inversely to bond prices when rates change.
    This relationship is fundamental to fixed income investing.
    Duration risk becomes more important in rising rate environments.
    """

    chunker = TextChunker(chunk_size=300, chunk_overlap=30, strategy="paragraph")
    chunks = chunker.chunk(test_text, {"source": "test"})

    print(f"Strategy: paragraph")
    print(f"Chunk size: 300, Overlap: 30")
    print(f"Number of chunks: {len(chunks)}")
    print("-" * 60)

    for chunk in chunks:
        print(f"\nChunk {chunk['chunk_index'] + 1}/{chunk['chunk_count']}:")
        print(f"Length: {chunk['char_length']} chars")
        print(f"Text: {chunk['text'][:100]}...")
