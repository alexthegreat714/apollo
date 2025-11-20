"""
metadata_filter_test.py - RAG Metadata Filtering Test

Tests querying by date range, document type, and source.

Usage:
    cd C:\\Users\\blyth\\Desktop\\Engineering\\Apollo
    python apollo_tests\\metadata_filter_test.py
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chromadb
from datetime import datetime, timedelta
from typing import Dict, Any

# Import embedding function from retriever to ensure consistency
from apollo_rag.retriever import _get_embedding_function


def run_metadata_filter_test(
    persist_dir: str = "./test_stress_db",
    collection_name: str = "stress_test"
) -> Dict[str, Any]:
    """
    Test metadata filtering capabilities.

    Args:
        persist_dir: ChromaDB persist directory
        collection_name: Collection name

    Returns:
        Test results
    """
    print("=" * 60)
    print("RAG Metadata Filter Test")
    print("=" * 60)

    # Connect
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
    print(f"Document count: {doc_count}\n")

    test_results = []

    # Test 1: Filter by document kind
    print("Test 1: Filter by document kind (news)")
    print("-" * 60)

    results = collection.query(
        query_texts=["market news"],
        n_results=10,
        where={"kind": "news"}
    )

    news_count = len(results["documents"][0]) if results["documents"] else 0
    all_news = all(
        meta.get("kind") == "news"
        for meta in results["metadatas"][0]
    ) if results["metadatas"][0] else False

    print(f"  Results returned: {news_count}")
    print(f"  All results are 'news': {all_news}")
    test_results.append(("Filter by kind", all_news))

    # Test 2: Filter by source
    print("\nTest 2: Filter by source")
    print("-" * 60)

    results = collection.query(
        query_texts=["financial data"],
        n_results=10,
        where={"source": "Test Source 0"}
    )

    source_count = len(results["documents"][0]) if results["documents"] else 0
    all_source = all(
        meta.get("source") == "Test Source 0"
        for meta in results["metadatas"][0]
    ) if results["metadatas"][0] else True

    print(f"  Results returned: {source_count}")
    print(f"  All results from 'Test Source 0': {all_source}")
    test_results.append(("Filter by source", all_source))

    # Test 3: Filter by priority (numeric)
    print("\nTest 3: Filter by priority (<=5)")
    print("-" * 60)

    results = collection.query(
        query_texts=["important document"],
        n_results=10,
        where={"priority": {"$lte": 5}}
    )

    priority_count = len(results["documents"][0]) if results["documents"] else 0
    all_priority = all(
        meta.get("priority", 10) <= 5
        for meta in results["metadatas"][0]
    ) if results["metadatas"][0] else True

    print(f"  Results returned: {priority_count}")
    print(f"  All priorities <=5: {all_priority}")
    test_results.append(("Filter by priority", all_priority))

    # Test 4: Filter by multiple conditions (AND)
    print("\nTest 4: Filter by multiple conditions (kind=report AND priority<=5)")
    print("-" * 60)

    results = collection.query(
        query_texts=["quarterly report"],
        n_results=10,
        where={
            "$and": [
                {"kind": "report"},
                {"priority": {"$lte": 5}}
            ]
        }
    )

    multi_count = len(results["documents"][0]) if results["documents"] else 0
    all_multi = all(
        meta.get("kind") == "report" and meta.get("priority", 10) <= 5
        for meta in results["metadatas"][0]
    ) if results["metadatas"][0] else True

    print(f"  Results returned: {multi_count}")
    print(f"  All match conditions: {all_multi}")
    test_results.append(("Filter with AND", all_multi))

    # Test 5: Filter with OR conditions
    print("\nTest 5: Filter with OR (kind=news OR kind=report)")
    print("-" * 60)

    results = collection.query(
        query_texts=["financial information"],
        n_results=10,
        where={
            "$or": [
                {"kind": "news"},
                {"kind": "report"}
            ]
        }
    )

    or_count = len(results["documents"][0]) if results["documents"] else 0
    all_or = all(
        meta.get("kind") in ["news", "report"]
        for meta in results["metadatas"][0]
    ) if results["metadatas"][0] else True

    print(f"  Results returned: {or_count}")
    print(f"  All match OR conditions: {all_or}")
    test_results.append(("Filter with OR", all_or))

    # Test 6: Filter by kind exclusion
    print("\nTest 6: Exclude education documents")
    print("-" * 60)

    results = collection.query(
        query_texts=["financial data"],
        n_results=10,
        where={"kind": {"$ne": "education"}}
    )

    exclude_count = len(results["documents"][0]) if results["documents"] else 0
    none_education = all(
        meta.get("kind") != "education"
        for meta in results["metadatas"][0]
    ) if results["metadatas"][0] else True

    print(f"  Results returned: {exclude_count}")
    print(f"  None are 'education': {none_education}")
    test_results.append(("Filter with exclusion", none_education))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    passed_count = sum(1 for _, passed in test_results if passed)
    total_tests = len(test_results)

    for test_name, passed in test_results:
        status = "PASS" if passed else "FAIL"
        print(f"  {status}: {test_name}")

    all_passed = passed_count == total_tests

    print("\n" + "=" * 60)
    if all_passed:
        print(f"TEST PASSED: All {total_tests} filter tests passed")
    else:
        print(f"TEST PARTIAL: {passed_count}/{total_tests} tests passed")
    print("=" * 60)

    return {
        "total_tests": total_tests,
        "passed": passed_count,
        "results": test_results,
        "all_passed": all_passed
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Metadata Filter Test")
    parser.add_argument("--dir", default="./test_stress_db", help="Persist directory")
    parser.add_argument("--collection", default="stress_test", help="Collection name")

    args = parser.parse_args()

    run_metadata_filter_test(args.dir, args.collection)
