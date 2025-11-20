"""
rag_sanity_test.py
Quick gatekeeper test to verify the retriever + embedding pipeline.

Usage:
    cd C:\\Users\\blyth\\Desktop\\Engineering\\Apollo
    python rag_sanity_test.py
or
    run_apollo.bat
"""

import sys
from apollo_rag.retriever import get_retriever

# Test queries with expected document types
TEST_QUERIES = [
    {
        "query": "What is inflation and how does it affect investments?",
        "expected_kinds": ["education", "macro", "news", "report"],
        "description": "Inflation query"
    },
    {
        "query": "How do I diversify my portfolio?",
        "expected_kinds": ["education", "investing"],
        "description": "Diversification query"
    },
    {
        "query": "What are current interest rates?",
        "expected_kinds": ["news", "projection", "macro"],
        "description": "Interest rates query"
    },
    {
        "query": "Explain capital gains tax",
        "expected_kinds": ["education", "regulation", "tax"],
        "description": "Tax query"
    },
    {
        "query": "Stock market outlook 2025",
        "expected_kinds": ["projection", "news", "report"],
        "description": "Market outlook query"
    }
]

# Thresholds
MAX_ACCEPTABLE_DISTANCE = 0.8


def main():
    print("\n" + "=" * 70)
    print("APOLLO RAG SANITY TEST")
    print("=" * 70)

    passed = 0
    failed = 0
    errors = []

    try:
        retriever = get_retriever()
        stats = retriever.get_collection_stats()

        print(f"\nCollection: {stats.get('collection_name', 'unknown')}")
        print(f"Document count: {stats.get('document_count', 0)}")

        if stats.get('document_count', 0) == 0:
            print("\nERROR: Collection is empty!")
            print("Please ingest documents first with:")
            print("  python apollo_ingest\\ingest_any.py apollo_test_docs\\stress --recursive")
            sys.exit(1)

        print(f"\nRunning {len(TEST_QUERIES)} test queries...\n")
        print("-" * 70)

        for test in TEST_QUERIES:
            query = test["query"]
            expected_kinds = test["expected_kinds"]
            desc = test["description"]

            result = retriever.search(query, top_k=3, include_scores=True)
            results = result.get('results', [])

            # Check 1: Got at least one result
            has_results = len(results) >= 1

            # Check 2: Distance is acceptable
            distances_ok = all(
                r.get('distance', 1.0) < MAX_ACCEPTABLE_DISTANCE
                for r in results
            ) if results else False

            # Check 3: Check metadata exists
            has_metadata = all(
                r.get('meta', {}).get('kind') is not None
                for r in results
            ) if results else False

            # Determine pass/fail
            test_passed = has_results and distances_ok

            if test_passed:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = "FAIL"
                if not has_results:
                    errors.append(f"{desc}: No results returned")
                elif not distances_ok:
                    errors.append(f"{desc}: Distances too high")

            # Print result
            print(f"{status}: {desc}")
            print(f"  Query: {query[:50]}...")
            print(f"  Results: {len(results)}, Distances OK: {distances_ok}")

            if results:
                kinds = [r.get('meta', {}).get('kind', '?') for r in results]
                dists = [f"{r.get('distance', 0):.3f}" for r in results]
                print(f"  Kinds: {kinds}")
                print(f"  Distances: {dists}")
            print()

        # Summary
        print("-" * 70)
        print("SUMMARY")
        print("-" * 70)
        print(f"Queries run: {len(TEST_QUERIES)}")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")

        if errors:
            print(f"\nErrors:")
            for err in errors:
                print(f"  - {err}")

        print("\n" + "=" * 70)
        if failed == 0:
            print("TEST PASSED - RAG system is functioning correctly")
            print("=" * 70)
            sys.exit(0)
        else:
            print("TEST FAILED - Some queries did not meet thresholds")
            print("=" * 70)
            sys.exit(1)

    except Exception as e:
        print("\n" + "=" * 70)
        print("TEST ERROR")
        print("=" * 70)
        print(f"\nException: {e}")
        print("\nThis indicates the embedding function or collection config is incorrect.")
        print("Ensure APOLLO_EMBEDDING_MODEL=nomic-embed-text is set.")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
