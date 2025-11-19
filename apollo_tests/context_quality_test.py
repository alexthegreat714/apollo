"""
context_quality_test.py - RAG Context Quality Evaluation

Evaluates the coherence and relevance of retrieved chunks.
"""

import chromadb
from typing import Dict, Any, List
import re


# Quality check queries with expected content patterns
QUALITY_QUERIES = [
    {
        "query": "What is the current inflation rate?",
        "expected_patterns": ["inflation", "cpi", "%", "rate"],
        "min_relevance": 2,  # At least 2 patterns should match
    },
    {
        "query": "How do interest rates affect bonds?",
        "expected_patterns": ["interest", "rate", "bond", "yield", "duration"],
        "min_relevance": 2,
    },
    {
        "query": "Explain portfolio diversification",
        "expected_patterns": ["diversif", "portfolio", "asset", "risk", "correlation"],
        "min_relevance": 2,
    },
    {
        "query": "What are capital gains tax rates?",
        "expected_patterns": ["capital gain", "tax", "rate", "%", "long-term", "short-term"],
        "min_relevance": 2,
    },
    {
        "query": "Fed monetary policy outlook",
        "expected_patterns": ["fed", "rate", "policy", "fomc", "basis point"],
        "min_relevance": 2,
    },
]


def calculate_coherence_score(text: str) -> float:
    """
    Calculate a simple coherence score for text.

    Checks for:
    - Sentence structure
    - Word variety
    - No obvious truncation
    """
    if not text or len(text) < 50:
        return 0.0

    score = 0.0

    # Check for complete sentences
    sentences = re.split(r'[.!?]', text)
    complete_sentences = [s for s in sentences if len(s.strip()) > 10]
    if complete_sentences:
        score += 0.3

    # Check for word variety
    words = text.lower().split()
    unique_ratio = len(set(words)) / len(words) if words else 0
    if unique_ratio > 0.4:
        score += 0.3

    # Check for not truncated (ends with punctuation or complete thought)
    if text.strip()[-1] in '.!?':
        score += 0.2
    elif len(text) > 100:
        score += 0.1

    # Check for numeric data (often relevant in financial context)
    if re.search(r'\d+\.?\d*%?', text):
        score += 0.2

    return min(1.0, score)


def calculate_relevance_score(text: str, patterns: List[str]) -> Dict[str, Any]:
    """
    Calculate relevance score based on pattern matching.
    """
    text_lower = text.lower()
    matches = []

    for pattern in patterns:
        if pattern.lower() in text_lower:
            matches.append(pattern)

    score = len(matches) / len(patterns) if patterns else 0

    return {
        "score": score,
        "matches": matches,
        "total_patterns": len(patterns)
    }


def run_context_quality_test(
    persist_dir: str = "./test_stress_db",
    collection_name: str = "stress_test"
) -> Dict[str, Any]:
    """
    Test context quality of RAG retrievals.

    Args:
        persist_dir: ChromaDB persist directory
        collection_name: Collection name

    Returns:
        Quality test results
    """
    print("=" * 70)
    print("RAG Context Quality Test")
    print("=" * 70)

    # Connect
    client = chromadb.PersistentClient(path=persist_dir)

    try:
        collection = client.get_collection(name=collection_name)
    except Exception as e:
        print(f"ERROR: Collection not found: {e}")
        print("Run multi_insert_test.py first!")
        return {"error": str(e)}

    print(f"Collection: {collection_name}")
    print(f"Document count: {collection.count()}\n")

    results = []
    total_coherence = 0
    total_relevance = 0

    for i, test_case in enumerate(QUALITY_QUERIES):
        query = test_case["query"]
        patterns = test_case["expected_patterns"]
        min_relevance = test_case["min_relevance"]

        print(f"Query {i+1}: {query}")
        print("-" * 70)

        # Execute query
        query_result = collection.query(
            query_texts=[query],
            n_results=3,
            include=["documents", "metadatas", "distances"]
        )

        documents = query_result["documents"][0] if query_result["documents"] else []
        distances = query_result["distances"][0] if query_result["distances"] else []

        if not documents:
            print("  No results returned")
            results.append({
                "query": query,
                "coherence": 0,
                "relevance": 0,
                "passed": False
            })
            continue

        # Evaluate each result
        doc_scores = []
        for j, (doc, distance) in enumerate(zip(documents, distances)):
            coherence = calculate_coherence_score(doc)
            relevance = calculate_relevance_score(doc, patterns)

            doc_scores.append({
                "coherence": coherence,
                "relevance": relevance["score"],
                "matches": relevance["matches"],
                "distance": distance
            })

            print(f"  Result {j+1}:")
            print(f"    Coherence: {coherence:.2f}")
            print(f"    Relevance: {relevance['score']:.2f} (matched: {', '.join(relevance['matches'][:3])})")
            print(f"    Distance: {distance:.3f}")
            print(f"    Text: {doc[:80]}...")

        # Average scores for this query
        avg_coherence = sum(d["coherence"] for d in doc_scores) / len(doc_scores)
        avg_relevance = sum(d["relevance"] for d in doc_scores) / len(doc_scores)

        # Check if passes quality threshold
        best_relevance = max(d["relevance"] for d in doc_scores)
        passed = best_relevance >= (min_relevance / len(patterns))

        total_coherence += avg_coherence
        total_relevance += avg_relevance

        results.append({
            "query": query,
            "avg_coherence": avg_coherence,
            "avg_relevance": avg_relevance,
            "best_relevance": best_relevance,
            "passed": passed
        })

        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"\n  {status} - Avg Coherence: {avg_coherence:.2f}, Avg Relevance: {avg_relevance:.2f}\n")

    # Summary
    num_queries = len(QUALITY_QUERIES)
    overall_coherence = total_coherence / num_queries
    overall_relevance = total_relevance / num_queries
    passed_count = sum(1 for r in results if r["passed"])

    print("=" * 70)
    print("Quality Summary")
    print("=" * 70)
    print(f"Queries tested: {num_queries}")
    print(f"Queries passed: {passed_count}/{num_queries}")
    print(f"Overall coherence: {overall_coherence:.2f}")
    print(f"Overall relevance: {overall_relevance:.2f}")

    # Quality rating
    if overall_coherence >= 0.7 and overall_relevance >= 0.5:
        quality_rating = "GOOD"
    elif overall_coherence >= 0.5 and overall_relevance >= 0.3:
        quality_rating = "ACCEPTABLE"
    else:
        quality_rating = "NEEDS IMPROVEMENT"

    print(f"\nQuality Rating: {quality_rating}")

    all_passed = passed_count >= num_queries * 0.6

    print("\n" + "=" * 70)
    if all_passed:
        print("TEST PASSED: Context quality meets threshold")
    else:
        print("TEST NEEDS ATTENTION: Consider improving document quality or chunking")
    print("=" * 70)

    return {
        "num_queries": num_queries,
        "passed_count": passed_count,
        "overall_coherence": overall_coherence,
        "overall_relevance": overall_relevance,
        "quality_rating": quality_rating,
        "all_passed": all_passed,
        "results": results
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Context Quality Test")
    parser.add_argument("--dir", default="./test_stress_db", help="Persist directory")
    parser.add_argument("--collection", default="stress_test", help="Collection name")

    args = parser.parse_args()

    run_context_quality_test(args.dir, args.collection)
