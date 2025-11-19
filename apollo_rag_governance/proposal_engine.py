"""
proposal_engine.py - Internal RAG governance helper.

Generates maintenance proposals based on retrieval statistics and
metadata coverage. All actions are advisory only.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


class RAGProposalEngine:
    """Analyze RAG telemetry and produce human-reviewable proposals."""

    def __init__(self, log_path: Optional[Path] = None):
        root = Path(__file__).resolve().parents[1]
        default_log = root / "logs" / "rag_proposals.log"
        self.log_path = Path(log_path) if log_path else default_log
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self.precision_threshold = 0.4
        self.distance_threshold = 0.35
        self.category_target = 10
        self._retrieval_stats: Dict[str, Any] = {}
        self._metadata_distribution: Dict[str, Any] = {}

    def evaluate_retrieval_stats(self, stats: Dict[str, Any]) -> None:
        """Store retrieval statistics emitted by multi_query_test.py."""
        self._retrieval_stats = stats or {}

    def evaluate_metadata_distribution(self, dist: Dict[str, Any]) -> None:
        """Persist metadata distribution snapshots for later analysis."""
        self._metadata_distribution = dist or {}

    def generate_recommendations(self) -> Dict[str, Any]:
        """Create a proposal payload and append it to the governance log."""
        recommendations: List[Dict[str, str]] = []
        stats = self._retrieval_stats or {}
        distribution = self._metadata_distribution or {}

        self._propose_from_stats(stats, recommendations)
        self._propose_from_distribution(distribution, recommendations)

        payload = {
            "recommendations": recommendations,
            "priority": self._derive_priority(recommendations),
            "requires_user_confirmation": True,
        }

        self._log(payload)
        return payload

    def _propose_from_stats(self, stats: Dict[str, Any], out: List[Dict[str, str]]) -> None:
        results = stats.get("results") or []
        if not results:
            return

        total_queries = stats.get("total_queries") or len(results)
        high_distance = [r for r in results if (r.get("avg_distance") or 0) >= self.distance_threshold]
        zero_relevant = [r for r in results if (r.get("relevant_count") or 0) == 0]
        low_precision = [r for r in results if (r.get("precision") or 0) < self.precision_threshold]

        if high_distance:
            target_query = high_distance[0].get("query", "multiple queries")
            reason = (
                f"{len(high_distance)} queries exceeded distance "
                f"{self.distance_threshold:.2f}, indicating sparse vectors"
            )
            out.append(
                {
                    "action": "identify_high_distance_queries",
                    "target": target_query,
                    "reason": reason,
                }
            )

        overall_precision = stats.get("overall_precision")
        if overall_precision is not None and overall_precision < 0.35:
            out.append(
                {
                    "action": "update_embedding_model",
                    "target": stats.get("embedding_model", "embedding_pipeline"),
                    "reason": f"Overall precision {overall_precision:.1%} below 35% threshold",
                }
            )

        if len(zero_relevant) >= max(2, total_queries // 4):
            reason = f"{len(zero_relevant)} queries returned zero relevant documents"
            out.append(
                {
                    "action": "re_chunk",
                    "target": "rag_corpus",
                    "reason": reason,
                }
            )
        elif len(low_precision) >= max(3, total_queries // 3):
            reason = f"Low precision on {len(low_precision)} of {total_queries} queries"
            out.append(
                {
                    "action": "re_chunk",
                    "target": "rag_corpus",
                    "reason": reason,
                }
            )

        if zero_relevant:
            target_list = []
            for record in zero_relevant[:2]:
                query_text = record.get("query", "unknown")
                target_list.append(str(query_text)[:60])
            targets = ", ".join(target_list)
            out.append(
                {
                    "action": "re_ingest_file",
                    "target": targets or "multiple queries",
                    "reason": "Queries yielded no relevant context; investigate contributing files",
                }
            )

    def _propose_from_distribution(self, dist: Dict[str, Any], out: List[Dict[str, str]]) -> None:
        categories = dist.get("category_counts") or {}
        if categories:
            target = int(dist.get("category_target", self.category_target))
            underrepresented = [
                (name, count)
                for name, count in categories.items()
                if isinstance(count, (int, float)) and 0 < count < target
            ]
            low_frequency = [
                (name, count)
                for name, count in categories.items()
                if isinstance(count, (int, float)) and count <= max(2, target // 3)
            ]

            for name, count in underrepresented[:3]:
                out.append(
                    {
                        "action": "re_ingest_category",
                        "target": name,
                        "reason": f"Category underrepresented: {int(count)} docs, expected >= {target}",
                    }
                )

            if low_frequency:
                sparse = ", ".join(name for name, _ in low_frequency[:3])
                out.append(
                    {
                        "action": "expand_category",
                        "target": sparse,
                        "reason": "Low-frequency categories detected; broaden coverage",
                    }
                )

        sources = dist.get("source_counts") or {}
        if sources:
            flagged = dist.get("sources_needing_reingest") or []
            for source in flagged[:3]:
                out.append(
                    {
                        "action": "re_ingest_file",
                        "target": source,
                        "reason": "Source flagged by upstream ingestion audit",
                    }
                )

            low_sources = self._find_low_source_counts(sources)
            for source, count in low_sources[:2]:
                out.append(
                    {
                        "action": "re_ingest_file",
                        "target": source,
                        "reason": f"Only {count} chunks indexed; expected >= 3",
                    }
                )

        date_ranges = dist.get("date_ranges") or {}
        stale_docs = date_ranges.get("stale_documents") or date_ranges.get("stale_sources") or []
        for entry in stale_docs[:3]:
            if isinstance(entry, dict):
                target = entry.get("source") or entry.get("document") or entry.get("id") or "unknown"
                reason = entry.get("reason") or "Marked as stale by metadata audit"
            else:
                target = str(entry)
                reason = "Marked as stale by metadata audit"
            out.append(
                {
                    "action": "flag_stale_documents",
                    "target": target,
                    "reason": reason,
                }
            )

        oldest = date_ranges.get("oldest_document") or date_ranges.get("min")
        if oldest and not stale_docs:
            try:
                oldest_dt = datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
                if datetime.utcnow() - oldest_dt > timedelta(days=365):
                    out.append(
                        {
                            "action": "flag_stale_documents",
                            "target": str(oldest),
                            "reason": "Oldest document exceeds 12 months without refresh",
                        }
                    )
            except ValueError:
                pass

    def _find_low_source_counts(self, sources: Dict[str, Any]) -> List[tuple]:
        low_sources: List[tuple] = []
        for name, meta in sources.items():
            count = self._extract_count(meta)
            if count is not None and count < 3:
                low_sources.append((name, count))
        low_sources.sort(key=lambda item: item[1])
        return low_sources

    @staticmethod
    def _extract_count(value: Any) -> Optional[int]:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, dict):
            for key in ("documents", "chunks", "count", "value"):
                if key in value and isinstance(value[key], (int, float)):
                    return int(value[key])
        return None

    @staticmethod
    def _derive_priority(recommendations: List[Dict[str, str]]) -> str:
        if not recommendations:
            return "low"
        critical = {"update_embedding_model", "flag_stale_documents"}
        if len(recommendations) >= 4 or any(r["action"] in critical for r in recommendations):
            return "high"
        if len(recommendations) >= 2:
            return "medium"
        return "low"

    def _log(self, payload: Dict[str, Any]) -> None:
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "proposal": payload,
        }
        try:
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            # Logging failure should never block recommendation generation
            pass
