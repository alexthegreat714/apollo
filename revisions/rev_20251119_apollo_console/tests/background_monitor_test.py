"""
background_monitor_test.py - Test for RAG background monitor.

Tests the micro test set and monitoring functionality.
"""

import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_rag.monitor import RAGMonitor, MICRO_TEST_QUERIES


def test_micro_queries():
    """
    Test the 5 micro test queries for background monitoring.

    Verifies:
    - Retrieval returns non-empty results
    - Context is roughly relevant
    - Monitor logs results correctly
    """
    print("=" * 70)
    print("Background Monitor Micro Test")
    print("=" * 70)
    print(f"Running {len(MICRO_TEST_QUERIES)} micro test queries\n")

    monitor = RAGMonitor()

    # Run health check
    report = monitor.run_health_check()

    # Display results
    print("Health Check Report")
    print("-" * 70)

    # Collection stats
    stats = report.get("checks", {}).get("collection_stats", {})
    print(f"Collection: {stats.get('collection_name', 'unknown')}")
    print(f"Document count: {stats.get('document_count', 0)}")

    # Micro test results
    test_results = report.get("checks", {}).get("micro_tests", {})
    print(f"\nMicro Test Results:")
    print(f"  Queries run: {test_results.get('queries_run', 0)}")
    print(f"  Queries with results: {test_results.get('queries_with_results', 0)}")
    print(f"  Precision: {test_results.get('precision', 0):.1%}")
    print(f"  Coverage: {test_results.get('coverage', 0):.1%}")
    print(f"  Avg distance: {test_results.get('avg_distance', 0):.3f}")

    # Issues
    issues = report.get("issues", [])
    if issues:
        print(f"\nIssues Detected: {len(issues)}")
        for issue in issues:
            severity = issue.get("severity", "unknown")
            message = issue.get("message", "")
            print(f"  [{severity.upper()}] {message}")
    else:
        print("\nNo issues detected")

    # Test individual queries
    print("\n" + "-" * 70)
    print("Individual Query Tests")
    print("-" * 70)

    try:
        from apollo_rag.retriever import get_retriever
        retriever = get_retriever()

        passed = 0
        failed = 0

        for test in MICRO_TEST_QUERIES:
            query = test["query"]
            expected = test["expected_kinds"]
            min_results = test["min_results"]

            results = retriever.search(query=query, top_k=3)
            hits = results.get("results", [])

            # Check if we got minimum results
            has_results = len(hits) >= min_results

            # Check if any result has expected kind
            relevant = False
            for hit in hits:
                kind = hit.get("meta", {}).get("kind", "")
                if kind in expected:
                    relevant = True
                    break

            status = "PASS" if (has_results and relevant) else "FAIL"
            if status == "PASS":
                passed += 1
            else:
                failed += 1

            print(f"\n{status}: {query}")
            print(f"  Expected: {expected}")
            print(f"  Got: {[h.get('meta', {}).get('kind', '?') for h in hits]}")
            print(f"  Results: {len(hits)} (min: {min_results})")

        print("\n" + "=" * 70)
        print(f"Test Summary: {passed} passed, {failed} failed")
        print("=" * 70)

        return passed == len(MICRO_TEST_QUERIES)

    except Exception as e:
        print(f"\nERROR: {e}")
        print("Make sure the RAG collection is populated")
        return False


def test_monitor_start_stop():
    """Test monitor start/stop functionality."""
    print("\nTesting monitor start/stop...")

    monitor = RAGMonitor(interval_minutes=1)  # 1 minute for testing

    # Start monitor
    monitor.start()
    print("Monitor started")

    # Check it's running
    assert monitor._running, "Monitor should be running"

    # Stop monitor
    monitor.stop()
    print("Monitor stopped")

    assert not monitor._running, "Monitor should be stopped"

    print("Start/stop test: PASS")
    return True


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("Apollo Background Monitor Tests")
    print("=" * 70 + "\n")

    # Run tests
    micro_passed = test_micro_queries()
    startstop_passed = test_monitor_start_stop()

    # Summary
    print("\n" + "=" * 70)
    print("Final Results")
    print("=" * 70)
    print(f"Micro query tests: {'PASS' if micro_passed else 'FAIL'}")
    print(f"Start/stop test: {'PASS' if startstop_passed else 'FAIL'}")

    if micro_passed and startstop_passed:
        print("\nAll tests PASSED")
        sys.exit(0)
    else:
        print("\nSome tests FAILED")
        sys.exit(1)
