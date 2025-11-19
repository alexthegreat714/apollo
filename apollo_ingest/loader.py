"""
loader.py - CLI for Apollo Document Ingestion

Command-line interface for ingesting individual files into Apollo RAG.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_ingest.pdf_ingest import ingest_pdf
from apollo_ingest.csv_ingest import ingest_csv
from apollo_ingest.xlsx_ingest import ingest_xlsx
from apollo_ingest.text_ingest import ingest_text


def main():
    parser = argparse.ArgumentParser(
        description="Apollo Document Loader - Ingest files into RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python loader.py document.pdf
  python loader.py data.csv --kind statement
  python loader.py report.xlsx --source "Q4 Report"
  python loader.py notes.txt --chunk-size 256
        """
    )

    parser.add_argument("file", help="File to ingest")
    parser.add_argument("--kind", help="Document kind (report, statement, projection, regulation, news, education)")
    parser.add_argument("--source", help="Source name (defaults to filename)")
    parser.add_argument("--chunk-size", type=int, default=512, help="Chunk size in characters (default: 512)")
    parser.add_argument("--chunk-overlap", type=int, default=50, help="Chunk overlap (default: 50)")
    parser.add_argument("--rag-dir", help="ChromaDB persist directory")
    parser.add_argument("--collection", help="Collection name")
    parser.add_argument("--sheet", help="Sheet name for Excel files")
    parser.add_argument("--text-mode", choices=["narrative", "tabular"], default="narrative",
                        help="Text mode for CSV (default: narrative)")

    args = parser.parse_args()

    file_path = Path(args.file)

    if not file_path.exists():
        print(f"Error: File not found: {args.file}")
        sys.exit(1)

    suffix = file_path.suffix.lower()

    print(f"{'=' * 60}")
    print(f"Apollo Document Loader")
    print(f"{'=' * 60}")
    print(f"File: {file_path.name}")
    print(f"Type: {suffix}")
    print(f"{'=' * 60}")

    # Route to appropriate ingester
    if suffix == '.pdf':
        result = ingest_pdf(
            str(file_path),
            kind=args.kind,
            source=args.source,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            rag_dir=args.rag_dir,
            collection=args.collection
        )
    elif suffix == '.csv':
        result = ingest_csv(
            str(file_path),
            kind=args.kind,
            source=args.source,
            text_mode=args.text_mode,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            rag_dir=args.rag_dir,
            collection=args.collection
        )
    elif suffix in ['.xlsx', '.xls']:
        result = ingest_xlsx(
            str(file_path),
            sheet_name=args.sheet,
            kind=args.kind,
            source=args.source,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            rag_dir=args.rag_dir,
            collection=args.collection
        )
    elif suffix in ['.txt', '.md', '.json', '.text', '.rst']:
        result = ingest_text(
            str(file_path),
            kind=args.kind,
            source=args.source,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            rag_dir=args.rag_dir,
            collection=args.collection
        )
    else:
        print(f"Error: Unsupported file type: {suffix}")
        print("Supported types: .pdf, .csv, .xlsx, .xls, .txt, .md, .json")
        sys.exit(1)

    # Print result
    print(f"\n{'=' * 60}")
    print("Ingestion Result")
    print(f"{'=' * 60}")

    if "error" in result:
        print(f"ERROR: {result['error']}")
        sys.exit(1)
    else:
        for key, value in result.items():
            print(f"  {key}: {value}")
        print(f"\nStatus: SUCCESS")


if __name__ == "__main__":
    main()
