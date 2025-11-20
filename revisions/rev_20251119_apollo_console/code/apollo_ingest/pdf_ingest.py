"""
pdf_ingest.py - PDF Ingestion for Apollo RAG

Extracts text from PDF files and prepares for RAG ingestion.
"""

import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_schemas.validators import normalize_document, validate_document
from apollo_ingest.chunker import TextChunker
from apollo_ingest.rag_writer import RAGWriter


def extract_pdf_text(file_path: str) -> str:
    """
    Extract text from a PDF file.

    Args:
        file_path: Path to PDF file

    Returns:
        Extracted text content
    """
    try:
        import PyPDF2

        text_parts = []
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

        return '\n\n'.join(text_parts)

    except ImportError:
        # Fallback to pdfminer if PyPDF2 not available
        try:
            from pdfminer.high_level import extract_text
            return extract_text(file_path)
        except ImportError:
            raise ImportError("Please install PyPDF2 or pdfminer.six: pip install PyPDF2 pdfminer.six")


def infer_metadata(file_path: str, text: str) -> Dict[str, Any]:
    """
    Infer metadata from file and content.

    Args:
        file_path: Path to the file
        text: Extracted text content

    Returns:
        Inferred metadata dictionary
    """
    path = Path(file_path)

    # Get file stats
    stat = path.stat()
    created = datetime.fromtimestamp(stat.st_ctime)
    modified = datetime.fromtimestamp(stat.st_mtime)

    # Infer document kind from filename and content
    filename_lower = path.stem.lower()
    text_lower = text[:2000].lower()

    if any(word in filename_lower for word in ['report', 'analysis', 'review']):
        kind = 'report'
    elif any(word in filename_lower for word in ['statement', 'balance', 'income', 'cash']):
        kind = 'statement'
    elif any(word in filename_lower for word in ['forecast', 'projection', 'outlook']):
        kind = 'projection'
    elif any(word in filename_lower for word in ['regulation', 'rule', 'guidance', 'sec', 'irs']):
        kind = 'regulation'
    elif any(word in filename_lower for word in ['news', 'article', 'press']):
        kind = 'news'
    elif any(word in text_lower for word in ['learn', 'understand', 'guide', 'introduction']):
        kind = 'education'
    else:
        kind = 'report'  # Default

    return {
        'kind': kind,
        'source': path.stem,
        'timestamp': modified.isoformat(),
        'file_path': str(path.absolute()),
        'file_type': 'pdf',
        'file_size': stat.st_size,
    }


def ingest_pdf(
    file_path: str,
    kind: str = None,
    source: str = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    rag_dir: str = None,
    collection: str = None
) -> Dict[str, Any]:
    """
    Ingest a PDF file into Apollo RAG.

    Args:
        file_path: Path to PDF file
        kind: Document kind (auto-inferred if not provided)
        source: Source name (filename if not provided)
        chunk_size: Size of text chunks
        chunk_overlap: Overlap between chunks
        rag_dir: ChromaDB persist directory
        collection: Collection name

    Returns:
        Ingestion result dictionary
    """
    path = Path(file_path)

    if not path.exists():
        return {"error": f"File not found: {file_path}"}

    if path.suffix.lower() != '.pdf':
        return {"error": f"Not a PDF file: {file_path}"}

    print(f"[PDF] Extracting text from: {path.name}")

    # Extract text
    try:
        text = extract_pdf_text(file_path)
    except Exception as e:
        return {"error": f"Failed to extract PDF text: {e}"}

    if not text or len(text.strip()) < 50:
        return {"error": "PDF contains insufficient text content"}

    # Infer metadata
    meta = infer_metadata(file_path, text)
    if kind:
        meta['kind'] = kind
    if source:
        meta['source'] = source

    # Create summary (first 500 chars)
    summary = text[:500].strip()
    if len(text) > 500:
        summary = summary[:497] + "..."

    # Build document
    doc = {
        'kind': meta['kind'],
        'timestamp': meta['timestamp'],
        'source': meta['source'],
        'summary': summary,
        'full_text': text,
        'metadata': {
            'file_path': meta['file_path'],
            'file_type': meta['file_type'],
            'file_size': meta['file_size'],
        }
    }

    # Normalize and validate
    try:
        doc = normalize_document(doc, meta['kind'])
        is_valid, errors = validate_document(doc)
        if not is_valid:
            print(f"[PDF] Validation warnings: {errors}")
    except Exception as e:
        print(f"[PDF] Validation error: {e}")

    # Chunk the text
    chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = chunker.chunk(text, {
        'kind': meta['kind'],
        'source': meta['source'],
        'file_path': meta['file_path'],
    })

    print(f"[PDF] Created {len(chunks)} chunks")

    # Write to RAG
    writer = RAGWriter(persist_dir=rag_dir, collection_name=collection)
    result = writer.write_chunks(chunks, base_id=doc.get('id', path.stem))

    result['file'] = str(path.name)
    result['kind'] = meta['kind']
    result['text_length'] = len(text)
    result['chunks'] = len(chunks)

    print(f"[PDF] Ingestion complete: {result['count']} documents written")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest PDF into Apollo RAG")
    parser.add_argument("file", help="PDF file to ingest")
    parser.add_argument("--kind", help="Document kind (report, statement, etc.)")
    parser.add_argument("--source", help="Source name")
    parser.add_argument("--chunk-size", type=int, default=512, help="Chunk size")
    parser.add_argument("--chunk-overlap", type=int, default=50, help="Chunk overlap")
    parser.add_argument("--rag-dir", help="ChromaDB directory")
    parser.add_argument("--collection", help="Collection name")

    args = parser.parse_args()

    result = ingest_pdf(
        args.file,
        kind=args.kind,
        source=args.source,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        rag_dir=args.rag_dir,
        collection=args.collection
    )

    print("\nResult:")
    for key, value in result.items():
        print(f"  {key}: {value}")
