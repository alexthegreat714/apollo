from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass
class ScoreResult:
    score: float
    details: Dict[str, Any]


class ChatContextRAGEvaluator:
    def __init__(self, month: str) -> None:
        self.month = month
        self.repo_root = Path(__file__).resolve().parents[1]
        self.workspace_root = self.repo_root.parent
        timeout_s = float(os.getenv("CHAT_QUALITY_MODEL_TIMEOUT_S", "12"))
        timeout_sec = max(5, min(int(timeout_s), 60))
        os.environ.setdefault("APOLLO_DIALOG_TIMEOUT_SECS", str(timeout_sec))
        os.environ.setdefault("APOLLO_CHAT_TIMEOUT_SECS", str(timeout_sec))
        os.environ.setdefault("APOLLO_CHAT_DEEP_TIMEOUT_SECS", str(timeout_sec))
        import sys

        if str(self.repo_root) not in sys.path:
            sys.path.insert(0, str(self.repo_root))
        if str(self.workspace_root) not in sys.path:
            sys.path.insert(0, str(self.workspace_root))

        import app as apollo_app  # type: ignore

        self.app_module = apollo_app
        self.app_module.run_fino_classifier = lambda _message: {}
        self._install_model_timeout_guard()
        self.client = apollo_app.app.test_client()
        self._reset_month_state()
        self._seed_budget_state()

    def _install_model_timeout_guard(self) -> None:
        original_query_model = self.app_module.query_model

        def guarded_query_model(prompt: str, *args, **kwargs) -> str:
            try:
                timeout_s = float(os.getenv("CHAT_QUALITY_MODEL_TIMEOUT_S", "12"))
                timeout_sec = max(1, min(int(timeout_s), 60))
                kwargs.setdefault("timeout_sec", timeout_sec)
                kwargs.setdefault("retries", 0)
                return original_query_model(prompt, *args, **kwargs)
            except Exception:
                return "Model timeout during chat quality evaluation."

        self.app_module.query_model = guarded_query_model

    def _reset_month_state(self) -> None:
        from apollo_budget.store import get_store

        store = get_store()
        conn = store.connect()
        cur = conn.cursor()
        budget_id = f"budget-{self.month}"
        cur.execute("DELETE FROM budget_transaction WHERE month = ?", (self.month,))
        cur.execute("DELETE FROM import_batch WHERE month = ?", (self.month,))
        cur.execute("DELETE FROM insight WHERE month = ?", (self.month,))
        cur.execute("DELETE FROM paycheck WHERE budget_id = ?", (budget_id,))
        cur.execute("DELETE FROM goal")
        cur.execute("DELETE FROM category WHERE group_id IN (SELECT id FROM category_group WHERE budget_id = ?)", (budget_id,))
        cur.execute("DELETE FROM category_group WHERE budget_id = ?", (budget_id,))
        cur.execute("DELETE FROM budget WHERE id = ?", (budget_id,))
        conn.commit()

    def _seed_budget_state(self) -> None:
        category_rows = [
            {"group": "Food", "name": "Groceries", "assigned": 200.0, "due_date": "2026-02-20"},
            {"group": "Bills", "name": "Internet", "assigned": 120.0, "due_date": "2026-02-18"},
            {"group": "Housing", "name": "Rent", "assigned": 1400.0, "due_date": "2026-02-01"},
            {"group": "Transit", "name": "Fuel", "assigned": 160.0, "due_date": "2026-02-14"},
        ]
        for row in category_rows:
            payload = dict(row)
            payload["month"] = self.month
            self.client.post("/budget/category", json=payload)

        tx_rows = [
            {"date": "2026-02-01", "payee": "Landlord", "amount": -1400.0, "category": "Rent", "group": "Housing", "month": self.month},
            {"date": "2026-02-03", "payee": "Market A", "amount": -150.0, "category": "Groceries", "group": "Food", "month": self.month},
            {"date": "2026-02-10", "payee": "Market B", "amount": -95.0, "category": "Groceries", "group": "Food", "month": self.month},
            {"date": "2026-02-11", "payee": "ISP", "amount": -80.0, "category": "Internet", "group": "Bills", "month": self.month},
            {"date": "2026-02-13", "payee": "Gas Station", "amount": -72.0, "category": "Fuel", "group": "Transit", "month": self.month},
            {"date": "2026-02-15", "payee": "Payroll", "amount": 3200.0, "category": "Salary", "group": "Income", "month": self.month},
        ]
        for row in tx_rows:
            payload = dict(row)
            payload["source"] = "seed"
            self.client.post("/budget/transaction", json=payload)

        self.client.post(
            "/budget/goal",
            json={
                "name": "Emergency Fund",
                "target_amount": 12000.0,
                "saved_amount": 5400.0,
                "due_date": "2026-12-31",
            },
        )

    def _chat(self, message: str, conversation_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        payload = {
            "message": message,
            "intent": "personal_finance",
            "budget_focus": True,
            "budget_month": self.month,
            "ui_tab": "budget",
            "conversation_id": conversation_id,
        }
        resp = self.client.post("/chat", json=payload)
        body = resp.get_json(silent=True) or {}
        reply = str(body.get("reply") or "")
        return resp.status_code == 200 and bool(reply.strip()), reply, body

    def score_long_form_context(self) -> ScoreResult:
        conversation_id = "long-form-topic-eval"
        turns = [
            ("Start with groceries: what is happening this month?", ["grocer"]),
            ("How bad is that category and what should I do first?", ["grocer"]),
            ("Now compare it against internet spending.", ["internet", "grocer"]),
            ("Switch to my goals briefly and tell me status.", ["goal", "emergency"]),
            ("Return to the first category and give next step.", ["grocer"]),
            ("What due date matters next?", ["due", "2026-02"]),
            ("Does Market A contribute to the issue?", ["market a", "grocer"]),
            ("Keep going on groceries only, ignore other categories.", ["grocer"]),
            ("How much should move today to stabilize it?", ["grocer"]),
            ("Summarize our thread in 3 lines with the same topic focus.", ["grocer"]),
            ("Now if I say that category again, what do you assume?", ["grocer"]),
            ("Final check: what transaction date was the first grocery hit?", ["2026-02-03", "market a"]),
        ]

        details: List[Dict[str, Any]] = []
        expected_hits = 0
        actual_hits = 0
        failures = 0
        for idx, (message, expected_tokens) in enumerate(turns):
            ok, reply, _ = self._chat(message, conversation_id)
            low = reply.lower()
            hit_tokens = [tok for tok in expected_tokens if tok in low]
            expected_hits += len(expected_tokens)
            actual_hits += len(hit_tokens)
            if not ok:
                failures += 1
            details.append(
                {
                    "turn": idx + 1,
                    "message": message,
                    "ok": ok,
                    "expected_tokens": expected_tokens,
                    "matched_tokens": hit_tokens,
                    "reply_excerpt": reply[:220],
                }
            )

        token_match_rate = (actual_hits / expected_hits) if expected_hits else 0.0
        reliability_rate = (len(turns) - failures) / max(len(turns), 1)
        score = max(0.0, min(100.0, (token_match_rate * 75.0) + (reliability_rate * 25.0)))
        return ScoreResult(
            score=round(score, 2),
            details={
                "turn_count": len(turns),
                "token_match_rate": round(token_match_rate, 4),
                "reliability_rate": round(reliability_rate, 4),
                "turns": details,
            },
        )

    def score_rag_behavior(self) -> ScoreResult:
        # Wrap RAG search to count invocations and inject deterministic marker context.
        search_calls_per_turn: List[int] = []
        call_counter = {"current": 0}
        original_search = self.app_module.APOLLO_RAG.search

        marker_text = (
            "RAG Pull Marker: Travel category available -120.00 as of 2026-02-24. "
            "Action: move 120 from discretionary dining."
        )

        def wrapped_search(*args, **kwargs):
            call_counter["current"] += 1
            result = original_search(*args, **kwargs)
            results = list(result.get("results", []))
            results.insert(
                0,
                {
                    "text": marker_text,
                    "meta": {"kind": "budget_guidance", "source": "budget_guidance"},
                },
            )
            return {"results": results}

        self.app_module.APOLLO_RAG.search = wrapped_search
        try:
            prompts = [
                "Use the pulled context and explain what to do about travel overspend.",
                "Based on that pulled item, give the exact transfer amount.",
                "Do not fetch new data; summarize the pull in one paragraph.",
                "If I say that pull again, what was it?",
                "What date and category were in the pull?",
                "What action did the pull suggest?",
            ]
            replies: List[str] = []
            for prompt in prompts:
                call_counter["current"] = 0
                ok, reply, _ = self._chat(prompt, "rag-behavior-eval")
                replies.append(reply if ok else "")
                search_calls_per_turn.append(call_counter["current"])
        finally:
            self.app_module.APOLLO_RAG.search = original_search

        single_call_hits = sum(1 for n in search_calls_per_turn if n == 1)
        single_call_rate = single_call_hits / max(len(search_calls_per_turn), 1)

        grounded_hits = 0
        no_repull_hits = 0
        grounded_terms = ("travel", "-120", "2026-02-24", "dining")
        repull_patterns = (
            "need to pull",
            "need to fetch",
            "i will pull",
            "i will fetch",
            "let me pull",
            "let me fetch",
        )
        reply_details = []
        for idx, reply in enumerate(replies):
            low = reply.lower()
            grounded = any(term in low for term in grounded_terms)
            no_repull = not any(pattern in low for pattern in repull_patterns)
            if grounded:
                grounded_hits += 1
            if no_repull:
                no_repull_hits += 1
            reply_details.append(
                {
                    "turn": idx + 1,
                    "rag_search_calls": search_calls_per_turn[idx],
                    "grounded_in_pull": grounded,
                    "no_repull_language": no_repull,
                    "reply_excerpt": reply[:220],
                }
            )

        grounded_rate = grounded_hits / max(len(replies), 1)
        no_repull_rate = no_repull_hits / max(len(replies), 1)
        score = (single_call_rate * 60.0) + (grounded_rate * 30.0) + (no_repull_rate * 10.0)
        score = max(0.0, min(100.0, score))

        return ScoreResult(
            score=round(score, 2),
            details={
                "single_call_rate": round(single_call_rate, 4),
                "grounded_rate": round(grounded_rate, 4),
                "no_repull_rate": round(no_repull_rate, 4),
                "turns": reply_details,
            },
        )

    def run(self) -> Dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        context_score = self.score_long_form_context()
        rag_score = self.score_rag_behavior()

        return {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "month": self.month,
            "scores": {
                "chat_context_long_form_quality": {
                    "score_0_100": context_score.score,
                    "details": context_score.details,
                },
                "rag_single_pull_and_grounding_quality": {
                    "score_0_100": rag_score.score,
                    "details": rag_score.details,
                },
            },
            "overall_average_score_0_100": round((context_score.score + rag_score.score) / 2.0, 2),
        }


def main() -> int:
    month = os.getenv("CHAT_QUALITY_MONTH") or datetime.now(timezone.utc).strftime("%Y-%m")
    evaluator = ChatContextRAGEvaluator(month=month)
    payload = evaluator.run()
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
