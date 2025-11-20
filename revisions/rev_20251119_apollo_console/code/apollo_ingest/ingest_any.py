"""
ingest_any.py - Universal Ingestion CLI for Apollo RAG

Ingest multiple files or directories into Apollo RAG.
"""

import argparse
import sys
import os
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_ingest.pdf_ingest import ingest_pdf
from apollo_ingest.csv_ingest import ingest_csv
from apollo_ingest.xlsx_ingest import ingest_xlsx
from apollo_ingest.text_ingest import ingest_text


SUPPORTED_EXTENSIONS = {
    '.pdf': ingest_pdf,
    '.csv': ingest_csv,
    '.xlsx': ingest_xlsx,
    '.xls': ingest_xlsx,
    '.txt': ingest_text,
    '.md': ingest_text,
    '.json': ingest_text,
    '.text': ingest_text,
    '.rst': ingest_text,
}


def find_files(paths: List[str], recursive: bool = False) -> List[Path]:
    """
    Find all supported files from given paths.

    Args:
        paths: List of file or directory paths
        recursive: Search directories recursively

    Returns:
        List of file paths
    """
    files = []

    for path_str in paths:
        path = Path(path_str)

        if path.is_file():
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
            else:
                print(f"Warning: Skipping unsupported file: {path.name}")

        elif path.is_dir():
            if recursive:
                for ext in SUPPORTED_EXTENSIONS:
                    files.extend(path.rglob(f"*{ext}"))
            else:
                for ext in SUPPORTED_EXTENSIONS:
                    files.extend(path.glob(f"*{ext}"))

        else:
            print(f"Warning: Path not found: {path_str}")

    return sorted(set(files))


def ingest_file(
    file_path: Path,
    kind: str = None,
    source: str = None,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
    rag_dir: str = None,
    collection: str = None
) -> Dict[str, Any]:
    """
    Ingest a single file using the appropriate ingester.
    """
    suffix = file_path.suffix.lower()
    ingester = SUPPORTED_EXTENSIONS.get(suffix)

    if not ingester:
        return {"error": f"Unsupported file type: {suffix}"}

    return ingester(
        str(file_path),
        kind=kind,
        source=source,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        rag_dir=rag_dir,
        collection=collection
    )


def main():
    parser = argparse.ArgumentParser(
        description="Apollo Universal Ingestion - Ingest multiple files into RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python ingest_any.py file1.pdf file2.csv file3.xlsx
  python ingest_any.py ./documents/
  python ingest_any.py ./reports/ --recursive
  python ingest_any.py *.pdf --kind report
        """
    )

    parser.add_argument("paths", nargs="+", help="Files or directories to ingest")
    parser.add_argument("--recursive", "-r", action="store_true", help="Search directories recursively")
    parser.add_argument("--kind", help="Document kind for all files")
    parser.add_argument("--source", help="Source name for all files")
    parser.add_argument("--chunk-size", type=int, default=512, help="Chunk size (default: 512)")
    parser.add_argument("--chunk-overlap", type=int, default=50, help="Chunk overlap (default: 50)")
    parser.add_argument("--rag-dir", help="ChromaDB persist directory")
    parser.add_argument("--collection", help="Collection name")
    parser.add_argument("--dry-run", action="store_true", help="List files without ingesting")

    args = parser.parse_args()

    # Find all files
    files = find_files(args.paths, args.recursive)

    if not files:
        print("No supported files found.")
        print(f"Supported extensions: {', '.join(SUPPORTED_EXTENSIONS.keys())}")
        sys.exit(1)

    print(f"{'=' * 60}")
    print(f"Apollo Universal Ingestion")
    print(f"{'=' * 60}")
    print(f"Files found: {len(files)}")

    if args.dry_run:
        print("\nFiles to be ingested:")
        for f in files:
            print(f"  - {f}")
        print("\n(Dry run - no files ingested)")
        sys.exit(0)

    # Ingest each file
    results = []
    success_count = 0
    error_count = 0
    total_chunks = 0

    for i, file_path in enumerate(files):
        print(f"\n[{i+1}/{len(files)}] {file_path.name}")

        result = ingest_file(
            file_path,
            kind=args.kind,
            source=args.source,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            rag_dir=args.rag_dir,
            collection=args.collection
        )

        result['file_path'] = str(file_path)
        results.append(result)

        if "error" in result:
            error_count += 1
            print(f"  ERROR: {result['error']}")
        else:
            success_count += 1
            total_chunks += result.get('chunks', 0)
            print(f"  OK: {result.get('chunks', 0)} chunks")

    # Summary
    print(f"\n{'=' * 60}")
    print("Ingestion Summary")
    print(f"{'=' * 60}")
    print(f"Total files: {len(files)}")
    print(f"Successful: {success_count}")
    print(f"Failed: {error_count}")
    print(f"Total chunks: {total_chunks}")

    if error_count > 0:
        print("\nFailed files:")
        for r in results:
            if "error" in r:
                print(f"  - {r['file_path']}: {r['error']}")

    print(f"\nStatus: {'SUCCESS' if error_count == 0 else 'PARTIAL'}")


if __name__ == "__main__":
    main()
