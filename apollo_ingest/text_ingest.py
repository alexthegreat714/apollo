"""
text_ingest.py - Plain Text Ingestion for Apollo RAG

Ingests plain text files (.txt, .md, .json) into RAG.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_schemas.validators import normalize_document, validate_document
from apollo_ingest.chunker import TextChunker
from apollo_ingest.rag_writer import RAGWriter


def read_text_file(file_path: str) -> str:
    """
    Read text from a file.

    Args:
        file_path: Path to text file

    Returns:
        Text content
    """
    path = Path(file_path)

    # Try different encodings
    encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']

    for encoding in encodings:
        try:
            with open(path, 'r', encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue

    raise ValueError(f"Could not decode file with any of: {encodings}")


def extract_json_text(content: str) -> str:
    """
    Extract text from JSON content.

    Args:
        content: JSON string

    Returns:
        Extracted text
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return content

    def extract_strings(obj, depth=0):
        texts = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, str) and len(value) > 20:
                    texts.append(f"{key}: {value}")
                else:
                    texts.extend(extract_strings(value, depth + 1))
        elif isinstance(obj, list):
            for item in obj:
                texts.extend(extract_strings(item, depth + 1))
        elif isinstance(obj, str) and len(obj) > 20:
            texts.append(obj)
        return texts

    extracted = extract_strings(data)
    return '\n\n'.join(extracted)


def infer_metadata(file_path: str, text: str) -> Dict[str, Any]:
    """
    Infer metadata from text file and content.
    """
    path = Path(file_path)
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime)

    filename_lower = path.stem.lower()
    text_lower = text[:2000].lower()

    # Infer kind
    if path.suffix == '.md':
        if any(word in filename_lower for word in ['readme', 'guide', 'tutorial', 'learn']):
            kind = 'education'
        else:
            kind = 'report'
    elif path.suffix == '.json':
        if 'data' in filename_lower or 'config' in filename_lower:
            kind = 'report'
        else:
            kind = 'news'
    else:
        # Infer from content
        if any(word in text_lower for word in ['regulation', 'rule', 'compliance', 'sec ']):
            kind = 'regulation'
        elif any(word in text_lower for word in ['forecast', 'projection', 'estimate', 'expect']):
            kind = 'projection'
        elif any(word in text_lower for word in ['learn', 'understand', 'guide', 'introduction']):
            kind = 'education'
        elif any(word in text_lower for word in ['report', 'analysis', 'summary']):
            kind = 'report'
        else:
            kind = 'news'

    return {
        'kind': kind,
        'source': path.stem,
        'timestamp': modified.isoformat(),
        'file_path': str(path.absolute()),
        'file_type': path.suffix[1:] if path.suffix else 'txt',
        'file_size': stat.st_size,
    }


def ingest_text(
    file_path: str,
    kind: str = None,
    source: str = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    rag_dir: str = None,
    collection: str = None
) -> Dict[str, Any]:
    """
    Ingest a text file into Apollo RAG.

    Args:
        file_path: Path to text file
        kind: Document kind
        source: Source name
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

    valid_extensions = ['.txt', '.md', '.json', '.text', '.rst']
    if path.suffix.lower() not in valid_extensions:
        return {"error": f"Unsupported file type: {path.suffix}. Supported: {valid_extensions}"}

    print(f"[TEXT] Reading: {path.name}")

    # Read file
    try:
        text = read_text_file(file_path)
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}

    # Handle JSON specially
    if path.suffix.lower() == '.json':
        text = extract_json_text(text)

    if not text or len(text.strip()) < 50:
        return {"error": "File contains insufficient content"}

    # Infer metadata
    meta = infer_metadata(file_path, text)
    if kind:
        meta['kind'] = kind
    if source:
        meta['source'] = source

    # Create summary
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
            print(f"[TEXT] Validation warnings: {errors}")
    except Exception as e:
        print(f"[TEXT] Validation error: {e}")

    # Chunk the text
    chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = chunker.chunk(text, {
        'kind': meta['kind'],
        'source': meta['source'],
        'file_path': meta['file_path'],
    })

    print(f"[TEXT] Created {len(chunks)} chunks")

    # Write to RAG
    writer = RAGWriter(persist_dir=rag_dir, collection_name=collection)
    result = writer.write_chunks(chunks, base_id=doc.get('id', path.stem))

    result['file'] = str(path.name)
    result['kind'] = meta['kind']
    result['text_length'] = len(text)
    result['chunks'] = len(chunks)

    print(f"[TEXT] Ingestion complete: {result['count']} documents written")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest text file into Apollo RAG")
    parser.add_argument("file", help="Text file to ingest")
    parser.add_argument("--kind", help="Document kind")
    parser.add_argument("--source", help="Source name")
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--chunk-overlap", type=int, default=50)
    parser.add_argument("--rag-dir", help="ChromaDB directory")
    parser.add_argument("--collection", help="Collection name")

    args = parser.parse_args()

    result = ingest_text(
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
