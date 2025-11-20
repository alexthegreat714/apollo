"""
multi_insert_test.py - RAG Multi-Document Insertion Test

Inserts 20-50 sample financial documents to stress test the RAG system.

Usage:
    cd C:\\Users\\blyth\\Desktop\\Engineering\\Apollo
    python apollo_tests\\multi_insert_test.py
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb
import uuid
import random
from datetime import datetime, timedelta

# Import embedding function from retriever to ensure consistency
from apollo_rag.retriever import _get_embedding_function


# Sample documents by kind
SAMPLE_DOCUMENTS = {
    "report": [
        "Q4 2024 Earnings Report: Revenue increased 15% year-over-year to $4.2 billion. Net income rose to $890 million, driven by strong performance in cloud services and international markets.",
        "Annual Financial Review 2024: The company maintained healthy cash reserves of $12.5 billion while reducing long-term debt by $2.3 billion. Operating margins improved to 28%.",
        "Quarterly Performance Summary: EPS of $3.45 exceeded analyst expectations of $3.20. The company announced a 10% dividend increase and $5 billion share buyback program.",
        "Risk Assessment Report: Market volatility increased 23% in Q4. Credit exposure remains within acceptable limits. Liquidity ratios exceed regulatory requirements.",
        "Industry Analysis Report: Healthcare sector shows strong growth potential with 8% projected annual growth. Technology valuations remain elevated despite rate concerns.",
    ],
    "statement": [
        "Balance Sheet Summary: Total assets of $85.4 billion, liabilities of $42.1 billion, shareholder equity of $43.3 billion. Current ratio improved to 2.1x.",
        "Income Statement Q4: Revenue $4.2B, COGS $2.8B, Gross Profit $1.4B, Operating Expenses $650M, Net Income $525M after tax provisions.",
        "Cash Flow Statement: Operating cash flow of $1.8B, capital expenditures of $450M, free cash flow of $1.35B. Dividends paid totaled $320M.",
        "Statement of Equity Changes: Beginning equity $41.2B, net income addition $2.1B, treasury stock purchases ($800M), dividend distributions ($320M), ending equity $42.18B.",
    ],
    "projection": [
        "2025 Market Outlook: Expect S&P 500 to reach 5,200-5,400 range. Bond yields likely to moderate as Fed begins rate cuts in Q2. International equities may outperform.",
        "Economic Forecast Q1-Q2 2025: GDP growth projected at 2.3%. Inflation expected to decline to 2.5% by mid-year. Unemployment to remain below 4%.",
        "Corporate Earnings Projections: S&P 500 earnings growth estimated at 8-10% for 2025. Technology and healthcare sectors expected to lead. Margins may compress slightly.",
        "Interest Rate Projections: Fed Funds rate likely to decline 75-100 basis points by end of 2025. 10-year Treasury yield target range 3.5-3.8%.",
        "Currency Forecast: US Dollar expected to weaken 3-5% against major currencies as interest rate differentials narrow.",
    ],
    "regulation": [
        "SEC Rule 10b-5 Reminder: Prohibition against fraud in connection with purchase or sale of securities. Applies to all market participants.",
        "IRS Publication 550 Update: Investment income and expenses guidance for tax year 2024. Includes updated capital gains rates and wash sale rules.",
        "FINRA Notice 24-15: New requirements for customer account statements. Enhanced disclosure of fees and compensation required starting January 2025.",
        "Federal Reserve Regulation D: Reserve requirements for depository institutions. Current requirements set at 0% for all liability categories.",
    ],
    "news": [
        "Federal Reserve holds rates steady at 5.25-5.50% following December FOMC meeting. Chair Powell signals potential rate cuts in 2025 if inflation continues to moderate.",
        "Stock markets rally on strong employment report. S&P 500 gains 1.8%, Nasdaq up 2.3%. Job growth exceeded expectations with 256,000 new positions added.",
        "Oil prices surge 5% on Middle East tensions. Brent crude rises to $82 per barrel. Energy stocks lead market gains with XLE up 3.2%.",
        "Tesla announces Q4 deliveries of 484,000 vehicles, beating estimates. Stock jumps 8% in pre-market trading. Full-year deliveries reach 1.81 million.",
        "Bitcoin surpasses $95,000 as institutional adoption accelerates. Spot ETF inflows reach $3.2 billion in December. Ethereum also hits new highs.",
        "Inflation data shows CPI at 3.2% year-over-year, slightly above expectations. Core CPI remains at 3.8%. Markets digest implications for Fed policy.",
    ],
    "education": [
        "Understanding Compound Interest: Money grows exponentially over time when interest earns interest. The formula is A = P(1 + r/n)^(nt). Start early for maximum benefit.",
        "Diversification Explained: Spreading investments across asset classes reduces risk. Include stocks, bonds, real estate, and commodities. Correlation matters more than number of holdings.",
        "Dollar-Cost Averaging Guide: Invest fixed amounts at regular intervals regardless of price. Reduces impact of volatility. Particularly effective in volatile markets.",
        "Reading Financial Statements: Balance sheet shows assets and liabilities. Income statement shows revenue and expenses. Cash flow shows money movement. All three tell the complete story.",
        "Tax-Loss Harvesting Strategy: Sell losing investments to offset capital gains. Reinvest in similar (not identical) securities. Watch for wash sale rules within 30 days.",
        "Understanding Bond Duration: Measures sensitivity to interest rate changes. Higher duration means more price volatility. Short duration bonds are safer in rising rate environments.",
    ],
}


def generate_documents(count: int = 30) -> list:
    """
    Generate sample financial documents.

    Args:
        count: Number of documents to generate

    Returns:
        List of document dictionaries
    """
    documents = []
    kinds = list(SAMPLE_DOCUMENTS.keys())

    for i in range(count):
        kind = random.choice(kinds)
        samples = SAMPLE_DOCUMENTS[kind]
        text = random.choice(samples)

        # Generate random date within last 90 days
        days_ago = random.randint(0, 90)
        doc_date = datetime.now() - timedelta(days=days_ago)

        doc = {
            "id": f"{kind}_{uuid.uuid4().hex[:8]}",
            "text": text,
            "metadata": {
                "kind": kind,
                "source": f"Test Source {i % 5}",
                "date": doc_date.strftime("%Y-%m-%d"),
                "priority": random.randint(1, 10),
            }
        }
        documents.append(doc)

    return documents


def run_insertion_test(
    persist_dir: str = "./test_stress_db",
    collection_name: str = "stress_test",
    doc_count: int = 30
):
    """
    Run the multi-document insertion test.

    Args:
        persist_dir: ChromaDB persist directory
        collection_name: Collection name
        doc_count: Number of documents to insert
    """
    print("=" * 60)
    print("RAG Multi-Document Insertion Test")
    print("=" * 60)

    # Initialize client
    client = chromadb.PersistentClient(path=persist_dir)

    # Delete existing collection
    try:
        client.delete_collection(collection_name)
        print(f"Deleted existing collection: {collection_name}")
    except Exception:
        pass

    # Get embedding function from retriever for consistency
    embedding_fn = _get_embedding_function()

    # Create collection with embedding function
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"}
    )

    print(f"\nCollection created: {collection_name}")
    print(f"Target document count: {doc_count}")

    # Generate documents
    documents = generate_documents(doc_count)

    # Insert in batches
    batch_size = 10
    total_inserted = 0

    for i in range(0, len(documents), batch_size):
        batch = documents[i:i + batch_size]

        ids = [doc["id"] for doc in batch]
        texts = [doc["text"] for doc in batch]
        metadatas = [doc["metadata"] for doc in batch]

        collection.add(
            ids=ids,
            documents=texts,
            metadatas=metadatas
        )

        total_inserted += len(batch)
        print(f"  Inserted batch {i // batch_size + 1}: {len(batch)} documents")

    # Verify
    final_count = collection.count()

    # Count by kind
    kind_counts = {}
    for doc in documents:
        kind = doc["metadata"]["kind"]
        kind_counts[kind] = kind_counts.get(kind, 0) + 1

    print(f"\n{'=' * 60}")
    print("Insertion Results")
    print(f"{'=' * 60}")
    print(f"Documents inserted: {total_inserted}")
    print(f"Collection count: {final_count}")
    print(f"\nBy document type:")
    for kind, count in sorted(kind_counts.items()):
        print(f"  {kind}: {count}")

    # Test query
    print(f"\n{'=' * 60}")
    print("Test Query")
    print(f"{'=' * 60}")

    test_query = "What are the current interest rates and Fed policy?"
    results = collection.query(
        query_texts=[test_query],
        n_results=3
    )

    print(f"Query: {test_query}")
    print(f"Results returned: {len(results['documents'][0])}")

    for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
        print(f"\n  {i + 1}. [{meta.get('kind', 'unknown')}] {doc[:80]}...")

    print(f"\n{'=' * 60}")
    print(f"TEST PASSED: {total_inserted} documents inserted successfully")
    print(f"{'=' * 60}")

    return {
        "total_inserted": total_inserted,
        "final_count": final_count,
        "kind_counts": kind_counts
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Multi-Document Insertion Test")
    parser.add_argument("--count", type=int, default=30, help="Number of documents")
    parser.add_argument("--dir", default="./test_stress_db", help="Persist directory")
    parser.add_argument("--collection", default="stress_test", help="Collection name")

    args = parser.parse_args()

    run_insertion_test(args.dir, args.collection, args.count)
