"""
multi_query_test.py - RAG Multi-Query Test

Runs 20 queries against the RAG system and evaluates ranking results.

Usage:
    cd C:\\Users\\blyth\\Desktop\\Engineering\\Apollo
    python apollo_tests\\multi_query_test.py
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb
from typing import Dict, Any, List

# Import embedding function from retriever to ensure consistency
from apollo_rag.retriever import _get_embedding_function


# Test queries with expected relevant kinds
TEST_QUERIES = [
    ("What is the current inflation rate?", ["news", "report", "macro"]),
    ("How do I calculate compound interest?", ["education"]),
    ("What are the Fed's interest rate projections?", ["projection", "news"]),
    ("Show me Q4 earnings results", ["report", "statement"]),
    ("What are capital gains tax rules?", ["regulation", "tax"]),
    ("How should I diversify my portfolio?", ["education", "investing"]),
    ("What's happening in the stock market?", ["news", "markets"]),
    ("Explain dollar-cost averaging", ["education"]),
    ("What are the bond yield forecasts?", ["projection"]),
    ("How do I read a balance sheet?", ["education", "statement"]),
    ("What is the S&P 500 outlook?", ["projection", "news"]),
    ("Tell me about SEC regulations", ["regulation"]),
    ("What is the company's cash flow?", ["statement"]),
    ("How does tax-loss harvesting work?", ["education", "tax"]),
    ("What's the GDP growth forecast?", ["projection", "macro"]),
    ("Explain risk management strategies", ["education", "risk"]),
    ("What are Bitcoin price movements?", ["news", "crypto"]),
    ("How do I calculate Sharpe ratio?", ["education", "risk"]),
    ("What is the unemployment rate?", ["news", "report"]),
    ("Explain bond duration", ["education"]),
]


def run_query_test(
    persist_dir: str = "./test_stress_db",
    collection_name: str = "stress_test",
    top_k: int = 5
) -> Dict[str, Any]:
    """
    Run multi-query test against RAG system.

    Args:
        persist_dir: ChromaDB persist directory
        collection_name: Collection name
        top_k: Number of results per query

    Returns:
        Test results summary
    """
    print("=" * 70)
    print("RAG Multi-Query Test")
    print("=" * 70)

    # Connect to client
    client = chromadb.PersistentClient(path=persist_dir)

    # Get embedding function from retriever for consistency
    embedding_fn = _get_embedding_function()

    try:
        collection = client.get_collection(
            name=collection_name,
            embedding_function=embedding_fn
        )
    except Exception as e:
        print(f"ERROR: Collection not found: {e}")
        print("Run multi_insert_test.py first!")
        return {"error": str(e)}

    doc_count = collection.count()
    print(f"Collection: {collection_name}")
    print(f"Document count: {doc_count}")
    print(f"Running {len(TEST_QUERIES)} queries...\n")

    # Run queries
    results = []
    total_relevant = 0
    total_retrieved = 0

    for i, (query, expected_kinds) in enumerate(TEST_QUERIES):
        query_result = collection.query(
            query_texts=[query],
            n_results=top_k,
            include=["documents", "metadatas", "distances"]
        )

        # Analyze results
        retrieved_kinds = []
        relevant_count = 0
        distances = query_result.get("distances", [[]])[0]

        for j, meta in enumerate(query_result["metadatas"][0]):
            kind = meta.get("kind", "unknown")
            retrieved_kinds.append(kind)

            # Check if kind matches expected
            if kind in expected_kinds:
                relevant_count += 1

        # Calculate precision
        precision = relevant_count / top_k if top_k > 0 else 0
        total_relevant += relevant_count
        total_retrieved += top_k

        # Result entry
        result_entry = {
            "query": query,
            "expected_kinds": expected_kinds,
            "retrieved_kinds": retrieved_kinds,
            "relevant_count": relevant_count,
            "precision": precision,
            "avg_distance": sum(distances) / len(distances) if distances else 0,
        }
        results.append(result_entry)

        # Print result
        status = "PASS" if precision >= 0.4 else "PARTIAL" if precision > 0 else "FAIL"
        print(f"{status} Q{i+1}: {query[:50]}...")
        print(f"   Expected: {expected_kinds}")
        print(f"   Got: {retrieved_kinds}")
        print(f"   Precision: {precision:.1%}, Avg Distance: {result_entry['avg_distance']:.3f}")
        print()

    # Summary statistics
    overall_precision = total_relevant / total_retrieved if total_retrieved > 0 else 0
    high_precision = sum(1 for r in results if r["precision"] >= 0.4)
    some_relevant = sum(1 for r in results if r["relevant_count"] > 0)

    print("=" * 70)
    print("Summary")
    print("=" * 70)
    print(f"Total queries: {len(TEST_QUERIES)}")
    print(f"Overall precision: {overall_precision:.1%}")
    print(f"High precision (>=40%): {high_precision}/{len(TEST_QUERIES)}")
    print(f"Some relevant results: {some_relevant}/{len(TEST_QUERIES)}")

    # Average distance analysis
    avg_distances = [r["avg_distance"] for r in results]
    print(f"Average distance: {sum(avg_distances)/len(avg_distances):.3f}")
    print(f"Min distance: {min(avg_distances):.3f}")
    print(f"Max distance: {max(avg_distances):.3f}")

    # Pass/Fail
    passed = overall_precision >= 0.3 and some_relevant >= len(TEST_QUERIES) * 0.7

    print("\n" + "=" * 70)
    if passed:
        print("TEST PASSED: RAG retrieval performing adequately")
    else:
        print("TEST NEEDS ATTENTION: Consider improving embeddings or data")
    print("=" * 70)

    return {
        "total_queries": len(TEST_QUERIES),
        "overall_precision": overall_precision,
        "high_precision_count": high_precision,
        "some_relevant_count": some_relevant,
        "passed": passed,
        "results": results,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Multi-Query Test")
    parser.add_argument("--dir", default="./test_stress_db", help="Persist directory")
    parser.add_argument("--collection", default="stress_test", help="Collection name")
    parser.add_argument("--top-k", type=int, default=5, help="Results per query")

    args = parser.parse_args()

    run_query_test(args.dir, args.collection, args.top_k)
