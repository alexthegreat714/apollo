from __future__ import annotations

import csv
import concurrent.futures
import io
import json
import os
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


WEIGHTS = {
    "ui_parity_usability": 25.0,
    "data_correctness": 25.0,
    "rag_quality_actionability": 20.0,
    "context_retention": 20.0,
    "performance_reliability": 10.0,
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _safe_json(resp) -> Dict[str, Any]:
    try:
        return resp.get_json(silent=True) or {}
    except Exception:
        return {}


def _normalize_text(text: str) -> str:
    return (text or "").strip().lower()


@dataclass
class ScoreBlock:
    score: float
    max_score: float
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def pct(self) -> float:
        if self.max_score <= 0:
            return 0.0
        return (self.score / self.max_score) * 100.0


class BudgetE2EEvaluator:
    def __init__(self, month: str) -> None:
        self.month = month
        self.repo_root = Path(__file__).resolve().parents[1]
        self.workspace_root = self.repo_root.parent
        import sys

        if str(self.repo_root) not in sys.path:
            sys.path.insert(0, str(self.repo_root))
        if str(self.workspace_root) not in sys.path:
            sys.path.insert(0, str(self.workspace_root))
        import app as apollo_app  # type: ignore

        self.app_module = apollo_app
        self._install_model_timeout_guards()
        self.client = apollo_app.app.test_client()
        self._reset_month_state()

    def _reset_month_state(self) -> None:
        """Clear month-scoped budget data so evaluations are deterministic across reruns."""
        try:
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
        except Exception:
            # Non-fatal in case schema is unavailable before first bootstrap.
            pass

    def _install_model_timeout_guards(self) -> None:
        original_query_model = self.app_module.query_model

        def guarded_query_model(prompt: str, *args, **kwargs) -> str:
            timeout_s = float(os.getenv("BUDGET_EVAL_MODEL_TIMEOUT_S", "40"))
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(original_query_model, prompt, *args, **kwargs)
                result = future.result(timeout=timeout_s)
                executor.shutdown(wait=False, cancel_futures=True)
                return result
            except Exception:
                executor.shutdown(wait=False, cancel_futures=True)
                return "Model timeout during automated evaluation."

        self.app_module.query_model = guarded_query_model
        try:
            import apollo_budget.insights as budget_insights  # type: ignore

            budget_insights.query_model = guarded_query_model
        except Exception:
            pass

    def _make_csv(self, path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def _make_excel(self, path: Path, headers: List[str], rows: List[List[Any]]) -> None:
        try:
            import openpyxl  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"openpyxl required for tests: {exc}") from exc

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Budget"
        ws.append(headers)
        for row in rows:
            ws.append(row)
        wb.save(path)
        wb.close()

    def _post_chat(self, message: str) -> Tuple[bool, Dict[str, Any], str]:
        payload = {
            "message": message,
            "intent": "personal_finance",
            "budget_focus": True,
            "budget_month": self.month,
            "ui_tab": "budget",
            "conversation_id": "e2e-budget-eval",
        }
        try:
            resp = self.client.post("/chat", json=payload)
        except Exception as exc:
            return False, {}, f"chat_request_failed:{exc}"
        data = _safe_json(resp)
        if resp.status_code != 200:
            return False, data, f"chat_status_{resp.status_code}"
        reply = data.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            return False, data, "chat_empty_reply"
        return True, data, ""

    def score_ui_parity(self) -> ScoreBlock:
        index_html = (self.repo_root / "templates" / "index.html").read_text(encoding="utf-8")
        js_text = (self.repo_root / "static" / "apollo_ui.js").read_text(encoding="utf-8")
        css_text = (self.repo_root / "static" / "apollo_ui.css").read_text(encoding="utf-8")

        checks = {
            "tabs_overview": "data-tab=\"overview\"" in index_html,
            "tabs_chat": "data-tab=\"chat\"" in index_html,
            "tabs_budget": "data-tab=\"budget\"" in index_html,
            "tabs_reports": "data-tab=\"reports\"" in index_html,
            "tabs_cashflow": "data-tab=\"cashflow\"" in index_html,
            "tabs_goals": "data-tab=\"goals\"" in index_html,
            "tabs_imports": "data-tab=\"imports\"" in index_html,
            "tabs_insights": "data-tab=\"insights\"" in index_html,
            "tabs_settings": "data-tab=\"settings\"" in index_html,
            "ready_to_assign": "id=\"budget-ready\"" in index_html,
            "overspent_badge": "id=\"budget-overspent-count\"" in index_html,
            "category_table": "id=\"budget-category-list\"" in index_html,
            "accounts_panel": "id=\"budget-accounts-list\"" in index_html,
            "transactions_panel": "id=\"budget-transaction-list\"" in index_html,
            "manual_transaction": "id=\"transaction-add\"" in index_html,
            "receipt_import_button": "id=\"budget-receipt\"" in index_html,
            "receipt_import_chat_trigger": "shouldTriggerReceiptImport" in js_text,
            "guidance_panel": "id=\"budget-guidance\"" in index_html,
            "scenario_panel": "id=\"scenario-slider\"" in index_html,
            "paycheck_panel": "id=\"budget-paycheck-list\"" in index_html,
            "goals_panel": "id=\"goal-list\"" in index_html,
            "rail_navigation": "class=\"rail-nav\"" in index_html,
            "mobile_adaptation": "@media (max-width:" in css_text,
            "custom_typography": "Space+Grotesk" in index_html,
        }
        passed = sum(1 for value in checks.values() if value)
        score = WEIGHTS["ui_parity_usability"] * (passed / len(checks))
        return ScoreBlock(score=score, max_score=WEIGHTS["ui_parity_usability"], details={"checks": checks, "passed": passed})

    def score_data_correctness(self) -> ScoreBlock:
        details: Dict[str, Any] = {}
        points = 0.0
        step_value = WEIGHTS["data_correctness"] / 5.0

        with tempfile.TemporaryDirectory(prefix="apollo_budget_eval_") as tmp_dir:
            tmp = Path(tmp_dir)

            # 1) Missing column rejection for EveryDollar CSV
            bad_csv = tmp / "bad_missing_columns.csv"
            self._make_csv(
                bad_csv,
                rows=[{"Group": "Food", "Item": "Groceries", "Date": "2026-02-03", "Merchant": "Store A", "Amount": "10.00"}],
                fieldnames=["Group", "Item", "Date", "Merchant", "Amount"],
            )
            with bad_csv.open("rb") as handle:
                resp = self.client.post(
                    "/budget/import/everydollar_csv",
                    data={"month": self.month, "file": (io.BytesIO(handle.read()), bad_csv.name)},
                    content_type="multipart/form-data",
                )
            bad_json = _safe_json(resp)
            missing_ok = resp.status_code == 400 and bad_json.get("error") == "missing_columns"
            details["missing_columns_rejected"] = {"ok": missing_ok, "status": resp.status_code, "body": bad_json}
            if missing_ok:
                points += step_value

            # 2) Valid EveryDollar import and duplicate idempotence
            good_csv = tmp / "everydollar_feb.csv"
            valid_rows = [
                {"Group": "Income", "Item": "Salary", "Type": "Income", "Date": "2026-02-01", "Merchant": "Employer", "Amount": "3000.00"},
                {"Group": "Food", "Item": "Groceries", "Type": "Expense", "Date": "2026-02-03", "Merchant": "Market A", "Amount": "120.50"},
                {"Group": "Food", "Item": "Groceries", "Type": "Expense", "Date": "2026-02-10", "Merchant": "Market B", "Amount": "95.40"},
                {"Group": "Housing", "Item": "Rent", "Type": "Expense", "Date": "2026-02-01", "Merchant": "Landlord", "Amount": "1400.00"},
                {"Group": "Bills", "Item": "Internet", "Type": "Expense", "Date": "2026-02-15", "Merchant": "ISP", "Amount": "80.00"},
            ]
            self._make_csv(
                good_csv,
                rows=valid_rows,
                fieldnames=["Group", "Item", "Type", "Date", "Merchant", "Amount"],
            )
            with good_csv.open("rb") as handle:
                first_resp = self.client.post(
                    "/budget/import/everydollar_csv",
                    data={"month": self.month, "file": (io.BytesIO(handle.read()), good_csv.name)},
                    content_type="multipart/form-data",
                )
            first_json = _safe_json(first_resp)
            with good_csv.open("rb") as handle:
                second_resp = self.client.post(
                    "/budget/import/everydollar_csv",
                    data={"month": self.month, "file": (io.BytesIO(handle.read()), good_csv.name)},
                    content_type="multipart/form-data",
                )
            second_json = _safe_json(second_resp)
            idempotent_ok = (
                first_resp.status_code == 200
                and bool(first_json.get("imported", 0))
                and second_resp.status_code == 200
                and second_json.get("imported", -1) == 0
            )
            details["csv_idempotent"] = {
                "ok": idempotent_ok,
                "first": first_json,
                "second": second_json,
            }
            if idempotent_ok:
                points += step_value

            # 3) Excel ambiguous mapping rejection and explicit mapping success
            try:
                ambiguous_xlsx = tmp / "ambiguous.xlsx"
                self._make_excel(
                    ambiguous_xlsx,
                    headers=["Alpha", "Beta", "Gamma"],
                    rows=[["2026-02-11", "Coffee Shop", "-12.40"]],
                )
                with ambiguous_xlsx.open("rb") as handle:
                    amb_resp = self.client.post(
                        "/budget/import/excel",
                        data={"month": self.month, "file": (io.BytesIO(handle.read()), ambiguous_xlsx.name)},
                        content_type="multipart/form-data",
                    )
                amb_json = _safe_json(amb_resp)
                ambiguous_ok = amb_resp.status_code == 400 and amb_json.get("error") == "ambiguous_mapping"
                details["excel_ambiguous_rejected"] = {"ok": ambiguous_ok, "body": amb_json}

                mapped_xlsx = tmp / "mapped.xlsx"
                self._make_excel(
                    mapped_xlsx,
                    headers=["Date", "Merchant", "Amount", "Category", "Group", "Type", "Memo"],
                    rows=[
                        ["2026-02-12", "Gym", -45.00, "Fitness", "Lifestyle", "Expense", "Monthly membership"],
                        ["2026-02-13", "Freelance Client", 650.00, "Consulting", "Income", "Income", "Invoice 222"],
                    ],
                )
                explicit_mapping = {
                    "date": "Date",
                    "payee": "Merchant",
                    "amount": "Amount",
                    "category": "Category",
                    "group": "Group",
                    "type": "Type",
                    "memo": "Memo",
                }
                with mapped_xlsx.open("rb") as handle:
                    map_resp = self.client.post(
                        "/budget/import/excel",
                        data={
                            "month": self.month,
                            "mapping": json.dumps(explicit_mapping),
                            "file": (io.BytesIO(handle.read()), mapped_xlsx.name),
                        },
                        content_type="multipart/form-data",
                    )
                map_json = _safe_json(map_resp)
                mapping_ok = ambiguous_ok and map_resp.status_code == 200 and map_json.get("ok") is True
                details["excel_mapping_flow"] = {"ok": mapping_ok, "body": map_json}
                if mapping_ok:
                    points += step_value
            except RuntimeError as exc:
                details["excel_mapping_flow"] = {"ok": False, "skipped": True, "reason": str(exc)}

            # 4) Split transaction integrity
            split_payload = {
                "month": self.month,
                "date": "2026-02-18",
                "payee": "Big Box Store",
                "amount": -60.0,
                "category": "Split",
                "group": "General",
                "source": "manual",
                "splits": [
                    {"category": "Groceries", "group": "Food", "amount": -40.0, "payee": "Big Box Store"},
                    {"category": "Home", "group": "Household", "amount": -20.0, "payee": "Big Box Store"},
                ],
            }
            split_resp = self.client.post("/budget/transaction", json=split_payload)
            split_json = _safe_json(split_resp)
            tx_resp = self.client.get(f"/budget/transactions?month={self.month}")
            tx_json = _safe_json(tx_resp)
            split_parent_id = split_json.get("id")
            split_children = [t for t in (tx_json.get("transactions") or []) if t.get("split_parent_id") == split_parent_id]
            split_sum = sum(float(t.get("amount") or 0) for t in split_children)
            split_ok = split_resp.status_code == 200 and len(split_children) == 2 and abs(split_sum - (-60.0)) < 0.001
            details["split_integrity"] = {
                "ok": split_ok,
                "split_parent_id": split_parent_id,
                "split_children_count": len(split_children),
                "split_sum": split_sum,
            }
            if split_ok:
                points += step_value

            # 5) Overspent detection + context update check
            self.client.post(
                "/budget/category",
                json={"month": self.month, "group": "Food", "name": "Groceries", "assigned": 100.0, "due_date": "2026-02-20"},
            )
            summary_resp_before = self.client.get(f"/budget/summary?month={self.month}")
            summary_before = _safe_json(summary_resp_before)
            context_before_resp = self.client.get(f"/budget/context?month={self.month}")
            context_before = _safe_json(context_before_resp).get("context", "")

            self.client.post(
                "/budget/category",
                json={"month": self.month, "group": "Food", "name": "Groceries", "assigned": 600.0, "due_date": "2026-02-20"},
            )
            summary_resp_after = self.client.get(f"/budget/summary?month={self.month}")
            summary_after = _safe_json(summary_resp_after)
            context_after_resp = self.client.get(f"/budget/context?month={self.month}")
            context_after = _safe_json(context_after_resp).get("context", "")

            overspent_ok = (
                summary_before.get("overspent_count", 0) >= summary_after.get("overspent_count", 0)
                and summary_before.get("overspent_count", 0) > 0
                and str(context_before) != str(context_after)
            )
            details["overspent_and_context"] = {
                "ok": overspent_ok,
                "summary_before": summary_before,
                "summary_after": summary_after,
            }
            if overspent_ok:
                points += step_value

        return ScoreBlock(score=points, max_score=WEIGHTS["data_correctness"], details=details)

    def score_rag_quality(self) -> ScoreBlock:
        details: Dict[str, Any] = {}
        points = 0.0
        max_points = WEIGHTS["rag_quality_actionability"]

        refresh_start = time.perf_counter()
        refresh_resp = self.client.post(f"/budget/insights/refresh?month={self.month}", json={})
        refresh_ms = (time.perf_counter() - refresh_start) * 1000.0
        refresh_json = _safe_json(refresh_resp)
        details["refresh"] = {"status": refresh_resp.status_code, "body": refresh_json, "latency_ms": round(refresh_ms, 2)}

        insights_resp = self.client.get(f"/budget/insights?month={self.month}")
        insights_json = _safe_json(insights_resp)
        summary_text = str(insights_json.get("summary") or "")
        source_count = len(insights_json.get("sources") or [])
        action_terms = ("reduce", "increase", "allocate", "prioritize", "cut", "review", "plan", "track", "pay")
        action_hits = sum(1 for term in action_terms if term in _normalize_text(summary_text))

        refresh_ok = refresh_resp.status_code == 200 and bool(refresh_json.get("ok"))
        if refresh_ok:
            points += 6.0
        if len(summary_text.strip()) >= 180:
            points += 6.0
        if source_count >= 2:
            points += 4.0
        if action_hits >= 2:
            points += 4.0

        details["insights"] = {
            "status": insights_resp.status_code,
            "summary_chars": len(summary_text.strip()),
            "source_count": source_count,
            "action_term_hits": action_hits,
        }
        return ScoreBlock(score=_clamp(points, 0.0, max_points), max_score=max_points, details=details)

    def score_context_retention(self) -> ScoreBlock:
        details: Dict[str, Any] = {"turns": []}
        points = 0.0
        step = WEIGHTS["context_retention"] / 3.0

        # Normalize assignments so groceries is the dominant overspend signal in tests.
        assignment_seed = [
            {"group": "Housing", "name": "Rent", "assigned": 1600.0},
            {"group": "Bills", "name": "Internet", "assigned": 120.0},
            {"group": "General", "name": "Split", "assigned": 80.0},
            {"group": "Food", "name": "Groceries", "assigned": 100.0, "due_date": "2026-02-20"},
            {"group": "Household", "name": "Home", "assigned": 60.0},
        ]
        for row in assignment_seed:
            payload = dict(row)
            payload["month"] = self.month
            self.client.post("/budget/category", json=payload)

        scenarios = [
            {
                "name": "groceries_pronoun_reference",
                "messages": [
                    "Using my imported budget for this month, which category is overspent?",
                    "How much should I move to that category?",
                ],
                "checks": [("grocer", 0), ("grocer", 1)],
            },
            {
                "name": "switch_topics_and_return",
                "messages": [
                    "Compare groceries and internet spending in this month.",
                    "Now switch to my goals for a moment.",
                    "Go back to the first category and tell me what to do next.",
                ],
                "checks": [("grocer", 0), ("goal", 1), ("grocer", 2)],
            },
            {
                "name": "specific_transaction_memory",
                "messages": [
                    "Tell me what happened with transaction Market A on 2026-02-03.",
                ],
                "checks": [("market", 0)],
            },
        ]

        for scenario in scenarios:
            scenario_ok = True
            replies: List[str] = []
            for message in scenario["messages"]:
                ok, data, err = self._post_chat(message)
                reply = str(data.get("reply") or "")
                replies.append(reply)
                details["turns"].append({"scenario": scenario["name"], "message": message, "ok": ok, "error": err, "reply": reply[:220]})
                if not ok:
                    scenario_ok = False
                    break
            if scenario_ok:
                for term, idx in scenario["checks"]:
                    if term not in _normalize_text(replies[idx]):
                        scenario_ok = False
                        break
            if scenario_ok:
                points += step
            details[scenario["name"]] = {"ok": scenario_ok}

        return ScoreBlock(score=_clamp(points, 0.0, WEIGHTS["context_retention"]), max_score=WEIGHTS["context_retention"], details=details)

    def score_performance_reliability(self) -> ScoreBlock:
        details: Dict[str, Any] = {}
        latencies: List[float] = []
        failures = 0

        endpoints = [
            f"/budget/summary?month={self.month}",
            f"/budget/categories?month={self.month}",
            f"/budget/transactions?month={self.month}",
            f"/budget/context?month={self.month}",
            f"/budget/goals",
        ]
        for endpoint in endpoints:
            start = time.perf_counter()
            resp = self.client.get(endpoint)
            elapsed = (time.perf_counter() - start) * 1000.0
            latencies.append(elapsed)
            if resp.status_code != 200:
                failures += 1

        avg_latency = statistics.mean(latencies) if latencies else 9999.0
        max_latency = max(latencies) if latencies else 9999.0

        # Latency scoring: full points at <= 250ms avg, linearly down to zero at 2000ms avg.
        latency_score = _clamp((2000.0 - avg_latency) / (2000.0 - 250.0), 0.0, 1.0) * 6.0
        reliability_score = _clamp((len(endpoints) - failures) / max(len(endpoints), 1), 0.0, 1.0) * 4.0
        score = latency_score + reliability_score

        details["latencies_ms"] = [round(v, 2) for v in latencies]
        details["avg_latency_ms"] = round(avg_latency, 2)
        details["max_latency_ms"] = round(max_latency, 2)
        details["failures"] = failures
        details["latency_score"] = round(latency_score, 2)
        details["reliability_score"] = round(reliability_score, 2)
        return ScoreBlock(score=_clamp(score, 0.0, WEIGHTS["performance_reliability"]), max_score=WEIGHTS["performance_reliability"], details=details)

    def run(self) -> Dict[str, Any]:
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        results = {
            "ui_parity_usability": self.score_ui_parity(),
            "data_correctness": self.score_data_correctness(),
            "rag_quality_actionability": self.score_rag_quality(),
            "context_retention": self.score_context_retention(),
            "performance_reliability": self.score_performance_reliability(),
        }

        total = sum(block.score for block in results.values())
        categories = {}
        fail_categories: List[str] = []
        for key, block in results.items():
            pct = block.pct
            categories[key] = {
                "score": round(block.score, 2),
                "max_score": round(block.max_score, 2),
                "percent": round(pct, 2),
                "pass_threshold_70": pct >= 70.0,
                "details": block.details,
            }
            if pct < 70.0:
                fail_categories.append(key)

        payload = {
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "month": self.month,
            "rubric_weights": WEIGHTS,
            "categories": categories,
            "final_score_0_100": round(total, 2),
            "overall_pass": len(fail_categories) == 0,
            "failed_categories": fail_categories,
        }
        return payload


def main() -> int:
    month = os.getenv("BUDGET_EVAL_MONTH") or datetime.now(timezone.utc).strftime("%Y-%m")
    evaluator = BudgetE2EEvaluator(month=month)
    result = evaluator.run()
    print(json.dumps(result, indent=2))
    return 0 if result.get("overall_pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
