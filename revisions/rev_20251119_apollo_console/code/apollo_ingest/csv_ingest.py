"""
csv_ingest.py - CSV Ingestion for Apollo RAG

Converts CSV data to text documents and prepares for RAG ingestion.
"""

import csv
import os
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_schemas.validators import normalize_document, validate_document
from apollo_ingest.chunker import TextChunker
from apollo_ingest.rag_writer import RAGWriter


def read_csv(file_path: str) -> List[Dict[str, str]]:
    """
    Read CSV file into list of row dictionaries.

    Args:
        file_path: Path to CSV file

    Returns:
        List of row dictionaries
    """
    rows = []
    with open(file_path, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def rows_to_text(rows: List[Dict[str, str]], text_mode: str = "narrative") -> str:
    """
    Convert CSV rows to text format.

    Args:
        rows: List of row dictionaries
        text_mode: 'narrative' for prose, 'tabular' for structured

    Returns:
        Text representation of the data
    """
    if not rows:
        return ""

    if text_mode == "tabular":
        # Create tabular text representation
        headers = list(rows[0].keys())
        lines = [" | ".join(headers)]
        lines.append("-" * len(lines[0]))
        for row in rows:
            line = " | ".join(str(row.get(h, "")) for h in headers)
            lines.append(line)
        return "\n".join(lines)

    else:  # narrative mode
        # Create narrative text from each row
        paragraphs = []
        for i, row in enumerate(rows):
            parts = []
            for key, value in row.items():
                if value and str(value).strip():
                    parts.append(f"{key}: {value}")
            if parts:
                paragraphs.append(f"Record {i+1}: " + ". ".join(parts) + ".")
        return "\n\n".join(paragraphs)


def infer_metadata(file_path: str, rows: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Infer metadata from CSV file and content.

    Args:
        file_path: Path to the file
        rows: CSV rows

    Returns:
        Inferred metadata dictionary
    """
    path = Path(file_path)
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime)

    # Infer kind from filename and headers
    filename_lower = path.stem.lower()
    headers = list(rows[0].keys()) if rows else []
    headers_lower = [h.lower() for h in headers]

    if any(word in filename_lower for word in ['transaction', 'trades', 'orders']):
        kind = 'statement'
    elif any(word in filename_lower for word in ['price', 'quote', 'ticker', 'stock']):
        kind = 'news'
    elif any(word in filename_lower for word in ['forecast', 'projection', 'estimate']):
        kind = 'projection'
    elif any(h in headers_lower for h in ['date', 'amount', 'balance', 'debit', 'credit']):
        kind = 'statement'
    else:
        kind = 'report'

    return {
        'kind': kind,
        'source': path.stem,
        'timestamp': modified.isoformat(),
        'file_path': str(path.absolute()),
        'file_type': 'csv',
        'file_size': stat.st_size,
        'row_count': len(rows),
        'columns': headers,
    }


def ingest_csv(
    file_path: str,
    kind: str = None,
    source: str = None,
    text_mode: str = "narrative",
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    rag_dir: str = None,
    collection: str = None
) -> Dict[str, Any]:
    """
    Ingest a CSV file into Apollo RAG.

    Args:
        file_path: Path to CSV file
        kind: Document kind
        source: Source name
        text_mode: 'narrative' or 'tabular'
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

    if path.suffix.lower() != '.csv':
        return {"error": f"Not a CSV file: {file_path}"}

    print(f"[CSV] Reading: {path.name}")

    # Read CSV
    try:
        rows = read_csv(file_path)
    except Exception as e:
        return {"error": f"Failed to read CSV: {e}"}

    if not rows:
        return {"error": "CSV contains no data rows"}

    # Convert to text
    text = rows_to_text(rows, text_mode)

    if not text or len(text.strip()) < 50:
        return {"error": "CSV contains insufficient content"}

    # Infer metadata
    meta = infer_metadata(file_path, rows)
    if kind:
        meta['kind'] = kind
    if source:
        meta['source'] = source

    # Create summary
    summary = f"CSV data with {meta['row_count']} rows and {len(meta['columns'])} columns: {', '.join(meta['columns'][:5])}"
    if len(meta['columns']) > 5:
        summary += f" and {len(meta['columns']) - 5} more"

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
            'row_count': meta['row_count'],
            'columns': ','.join(meta['columns']),
        }
    }

    # Normalize and validate
    try:
        doc = normalize_document(doc, meta['kind'])
        is_valid, errors = validate_document(doc)
        if not is_valid:
            print(f"[CSV] Validation warnings: {errors}")
    except Exception as e:
        print(f"[CSV] Validation error: {e}")

    # Chunk the text
    chunker = TextChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    chunks = chunker.chunk(text, {
        'kind': meta['kind'],
        'source': meta['source'],
        'file_path': meta['file_path'],
    })

    print(f"[CSV] Created {len(chunks)} chunks from {meta['row_count']} rows")

    # Write to RAG
    writer = RAGWriter(persist_dir=rag_dir, collection_name=collection)
    result = writer.write_chunks(chunks, base_id=doc.get('id', path.stem))

    result['file'] = str(path.name)
    result['kind'] = meta['kind']
    result['text_length'] = len(text)
    result['chunks'] = len(chunks)
    result['rows'] = meta['row_count']

    print(f"[CSV] Ingestion complete: {result['count']} documents written")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest CSV into Apollo RAG")
    parser.add_argument("file", help="CSV file to ingest")
    parser.add_argument("--kind", help="Document kind")
    parser.add_argument("--source", help="Source name")
    parser.add_argument("--text-mode", choices=["narrative", "tabular"], default="narrative")
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--chunk-overlap", type=int, default=50)
    parser.add_argument("--rag-dir", help="ChromaDB directory")
    parser.add_argument("--collection", help="Collection name")

    args = parser.parse_args()

    result = ingest_csv(
        args.file,
        kind=args.kind,
        source=args.source,
        text_mode=args.text_mode,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        rag_dir=args.rag_dir,
        collection=args.collection
    )

    print("\nResult:")
    for key, value in result.items():
        print(f"  {key}: {value}")
