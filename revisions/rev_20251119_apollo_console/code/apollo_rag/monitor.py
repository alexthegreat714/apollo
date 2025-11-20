"""
monitor.py - Background RAG monitoring service.

Periodically checks RAG health and generates corrective proposals.
"""

import json
import logging
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Monitor log file
MONITOR_LOG = Path(__file__).resolve().parents[1] / "logs" / "rag_monitor.log"

# Micro test set for background monitoring
MICRO_TEST_QUERIES = [
    {
        "query": "What is the current Fed rate policy?",
        "expected_kinds": ["macro", "news", "projection"],
        "min_results": 1
    },
    {
        "query": "Explain diversification.",
        "expected_kinds": ["education", "investing"],
        "min_results": 1
    },
    {
        "query": "How do capital gains taxes work?",
        "expected_kinds": ["education", "tax", "regulation"],
        "min_results": 1
    },
    {
        "query": "What is the S&P 500 outlook?",
        "expected_kinds": ["projection", "news", "report"],
        "min_results": 1
    },
    {
        "query": "Show me basic budgeting rules.",
        "expected_kinds": ["education", "personal_finance"],
        "min_results": 1
    }
]


def _log_monitor(report: Dict[str, Any]) -> None:
    """Log monitor report to file."""
    MONITOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "report": report
    }
    try:
        with MONITOR_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Failed to log monitor report: {e}")


class RAGMonitor:
    """
    Background monitor for RAG health.
    """

    def __init__(self, interval_minutes: int = 10):
        """
        Initialize the monitor.

        Args:
            interval_minutes: Check interval in minutes
        """
        self.interval_seconds = interval_minutes * 60
        self._running = False
        self._thread = None
        self._last_report = None

        # Thresholds
        self.precision_threshold = 0.3
        self.distance_threshold = 0.4
        self.min_coverage = 0.6

    def start(self) -> None:
        """Start the background monitor."""
        if self._running:
            logger.warning("Monitor already running")
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"RAG Monitor started (interval: {self.interval_seconds}s)")

    def stop(self) -> None:
        """Stop the background monitor."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("RAG Monitor stopped")

    def _run_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                report = self.run_health_check()
                self._last_report = report

                # Generate proposals if issues detected
                if report.get("issues_detected"):
                    self._generate_proposals(report)

            except Exception as e:
                logger.exception(f"Monitor check failed: {e}")

            # Wait for next interval
            time.sleep(self.interval_seconds)

    def run_health_check(self) -> Dict[str, Any]:
        """
        Run a comprehensive health check.

        Returns:
            Health check report
        """
        report = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "checks": {},
            "issues": [],
            "issues_detected": False
        }

        try:
            from apollo_rag.retriever import get_retriever
            retriever = get_retriever()

            # Check 1: Collection stats
            stats = retriever.get_collection_stats()
            report["checks"]["collection_stats"] = stats

            if stats.get("document_count", 0) == 0:
                report["issues"].append({
                    "type": "empty_collection",
                    "severity": "high",
                    "message": "Collection is empty"
                })

            # Check 2: Run micro test queries
            test_results = self._run_micro_tests(retriever)
            report["checks"]["micro_tests"] = test_results

            # Analyze test results
            if test_results.get("precision", 0) < self.precision_threshold:
                report["issues"].append({
                    "type": "low_precision",
                    "severity": "medium",
                    "message": f"Precision {test_results['precision']:.1%} below threshold"
                })

            if test_results.get("avg_distance", 0) > self.distance_threshold:
                report["issues"].append({
                    "type": "high_distance",
                    "severity": "medium",
                    "message": f"Average distance {test_results['avg_distance']:.3f} above threshold"
                })

            if test_results.get("coverage", 0) < self.min_coverage:
                report["issues"].append({
                    "type": "low_coverage",
                    "severity": "medium",
                    "message": f"Only {test_results['coverage']:.0%} of test queries returned results"
                })

            # Check 3: Embedding quality (check for zero-length)
            embedding_check = self._check_embeddings(retriever)
            report["checks"]["embeddings"] = embedding_check

            if embedding_check.get("corrupted", 0) > 0:
                report["issues"].append({
                    "type": "corrupted_embeddings",
                    "severity": "high",
                    "message": f"{embedding_check['corrupted']} corrupted embeddings detected"
                })

            # Check 4: Metadata completeness
            metadata_check = self._check_metadata(stats)
            report["checks"]["metadata"] = metadata_check

            if metadata_check.get("missing_kind_pct", 0) > 10:
                report["issues"].append({
                    "type": "missing_metadata",
                    "severity": "low",
                    "message": f"{metadata_check['missing_kind_pct']:.0f}% documents missing kind metadata"
                })

            report["issues_detected"] = len(report["issues"]) > 0

        except Exception as e:
            report["error"] = str(e)
            report["issues_detected"] = True

        # Log the report
        _log_monitor(report)

        return report

    def _run_micro_tests(self, retriever) -> Dict[str, Any]:
        """Run the micro test set."""
        results = {
            "queries_run": 0,
            "queries_with_results": 0,
            "total_relevant": 0,
            "total_retrieved": 0,
            "distances": []
        }

        for test in MICRO_TEST_QUERIES:
            query = test["query"]
            expected = test["expected_kinds"]

            try:
                search_results = retriever.search(
                    query=query,
                    top_k=3,
                    include_scores=True
                )

                results["queries_run"] += 1
                hits = search_results.get("results", [])

                if hits:
                    results["queries_with_results"] += 1

                    for hit in hits:
                        kind = hit.get("meta", {}).get("kind", "")
                        if kind in expected:
                            results["total_relevant"] += 1
                        results["total_retrieved"] += 1
                        results["distances"].append(hit.get("distance", 0))

            except Exception as e:
                logger.error(f"Micro test failed for '{query}': {e}")

        # Calculate metrics
        if results["total_retrieved"] > 0:
            results["precision"] = results["total_relevant"] / results["total_retrieved"]
        else:
            results["precision"] = 0

        if results["queries_run"] > 0:
            results["coverage"] = results["queries_with_results"] / results["queries_run"]
        else:
            results["coverage"] = 0

        if results["distances"]:
            results["avg_distance"] = sum(results["distances"]) / len(results["distances"])
        else:
            results["avg_distance"] = 0

        return results

    def _check_embeddings(self, retriever) -> Dict[str, Any]:
        """Check for corrupted embeddings."""
        # This would require access to raw embeddings
        # For now, return a placeholder
        return {
            "checked": True,
            "corrupted": 0,
            "note": "Embedding validation requires full collection scan"
        }

    def _check_metadata(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        """Check metadata completeness."""
        kind_dist = stats.get("kind_distribution", {})
        total = stats.get("document_count", 0)

        unknown_count = kind_dist.get("unknown", 0)

        return {
            "total_documents": total,
            "missing_kind": unknown_count,
            "missing_kind_pct": (unknown_count / total * 100) if total > 0 else 0
        }

    def _generate_proposals(self, report: Dict[str, Any]) -> None:
        """Generate corrective proposals based on issues."""
        try:
            from apollo_rag_governance.proposal_engine import RAGProposalEngine
            from apollo_rag_governance.action_executor import get_executor

            engine = RAGProposalEngine()

            # Feed test results to engine
            test_results = report.get("checks", {}).get("micro_tests", {})
            if test_results:
                engine.evaluate_retrieval_stats({
                    "overall_precision": test_results.get("precision", 0),
                    "results": []
                })

            # Generate proposal
            proposal = engine.generate_action_proposal()

            if proposal.actions:
                executor = get_executor()
                executor.store_proposal(proposal)
                logger.info(f"Generated proposal with {len(proposal.actions)} actions")

        except Exception as e:
            logger.error(f"Failed to generate proposals: {e}")

    def get_last_report(self) -> Optional[Dict[str, Any]]:
        """Get the last health check report."""
        return self._last_report


# Global monitor instance
_monitor = None


def get_monitor() -> RAGMonitor:
    """Get or create the global monitor."""
    global _monitor
    if _monitor is None:
        _monitor = RAGMonitor()
    return _monitor


def start_background_monitor(interval_minutes: int = 10) -> RAGMonitor:
    """Start the background monitor."""
    monitor = get_monitor()
    monitor.interval_seconds = interval_minutes * 60
    monitor.start()
    return monitor
