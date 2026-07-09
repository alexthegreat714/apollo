from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _utcnow() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _month_from_date(date_str: str) -> str:
    if not date_str:
        return datetime.utcnow().strftime("%Y-%m")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    # fallback to first 7 chars if looks like YYYY-MM
    if len(date_str) >= 7 and date_str[4] == "-":
        return date_str[:7]
    return datetime.utcnow().strftime("%Y-%m")


def _normalize_date(date_str: str) -> str:
    if not date_str:
        return datetime.utcnow().strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # As-is fallback
    return date_str.strip()


def _hash_row(parts: Iterable[str]) -> str:
    raw = "||".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def infer_excel_mapping(headers: Iterable[str]) -> Dict[str, str]:
    """Infer best-effort Excel column mapping from raw header names."""
    raw_headers = [str(h) for h in headers if h is not None]
    normalized = {h: _normalize_header(h) for h in raw_headers}
    mapping: Dict[str, str] = {}

    def pick(keys: List[str]) -> str:
        for raw, norm in normalized.items():
            if norm in keys:
                return raw
        for raw, norm in normalized.items():
            if any(key in norm for key in keys):
                return raw
        return ""

    date_col = pick(["date", "transactiondate", "posteddate", "postingdate"])
    if date_col:
        mapping["date"] = date_col

    payee_col = pick(["payee", "merchant", "description", "vendor", "name"])
    if payee_col:
        mapping["payee"] = payee_col

    amount_col = pick(["amount", "amt", "value", "total", "outflow", "inflow"])
    if amount_col:
        mapping["amount"] = amount_col

    category_col = pick(["category", "cat", "budgetcategory", "item"])
    if category_col:
        mapping["category"] = category_col

    group_col = pick(["group", "categorygroup", "bucket"])
    if group_col:
        mapping["group"] = group_col

    type_col = pick(["type", "transactiontype", "kind"])
    if type_col:
        mapping["type"] = type_col

    memo_col = pick(["memo", "note", "notes", "comment", "comments"])
    if memo_col:
        mapping["memo"] = memo_col

    account_col = pick(["account", "acct", "accountname"])
    if account_col:
        mapping["account"] = account_col

    return mapping


@dataclass
class BudgetStore:
    db_path: Path
    _conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def ensure_schema(self) -> None:
        conn = self.connect()
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS budget (
                id TEXT PRIMARY KEY,
                name TEXT,
                month TEXT,
                currency TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS category_group (
                id TEXT PRIMARY KEY,
                budget_id TEXT,
                name TEXT,
                ord INTEGER,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(budget_id) REFERENCES budget(id)
            );
            CREATE TABLE IF NOT EXISTS category (
                id TEXT PRIMARY KEY,
                group_id TEXT,
                name TEXT,
                assigned REAL,
                activity REAL,
                available REAL,
                target_type TEXT,
                target_value REAL,
                due_date TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(group_id) REFERENCES category_group(id)
            );
            CREATE TABLE IF NOT EXISTS account (
                id TEXT PRIMARY KEY,
                name TEXT,
                type TEXT,
                balance REAL,
                institution TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS budget_transaction (
                id TEXT PRIMARY KEY,
                date TEXT,
                month TEXT,
                payee TEXT,
                amount REAL,
                category_id TEXT,
                account_id TEXT,
                memo TEXT,
                split_parent_id TEXT,
                source TEXT,
                import_batch_id TEXT,
                hash TEXT,
                created_at TEXT,
                FOREIGN KEY(category_id) REFERENCES category(id),
                FOREIGN KEY(account_id) REFERENCES account(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_transaction_hash ON budget_transaction(hash);
            CREATE TABLE IF NOT EXISTS import_batch (
                id TEXT PRIMARY KEY,
                source TEXT,
                file_name TEXT,
                month TEXT,
                imported_at TEXT,
                hash TEXT
            );
            CREATE TABLE IF NOT EXISTS paycheck (
                id TEXT PRIMARY KEY,
                budget_id TEXT,
                date TEXT,
                amount REAL,
                source TEXT,
                created_at TEXT,
                FOREIGN KEY(budget_id) REFERENCES budget(id)
            );
            CREATE TABLE IF NOT EXISTS goal (
                id TEXT PRIMARY KEY,
                name TEXT,
                target_amount REAL,
                saved_amount REAL,
                due_date TEXT,
                category_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(category_id) REFERENCES category(id)
            );
            CREATE TABLE IF NOT EXISTS insight (
                id TEXT PRIMARY KEY,
                month TEXT,
                summary TEXT,
                sources_json TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS mapping_profile (
                id TEXT PRIMARY KEY,
                name TEXT,
                mapping_json TEXT,
                created_at TEXT
            );
            """
        )
        conn.commit()

    def _execute(self, query: str, params: Tuple[Any, ...] = ()) -> sqlite3.Cursor:
        conn = self.connect()
        cur = conn.cursor()
        cur.execute(query, params)
        conn.commit()
        return cur

    def _fetchall(self, query: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
        cur = self.connect().execute(query, params)
        return cur.fetchall()

    def _fetchone(self, query: str, params: Tuple[Any, ...] = ()) -> Optional[sqlite3.Row]:
        cur = self.connect().execute(query, params)
        return cur.fetchone()

    def upsert_budget(self, month: str, name: Optional[str] = None, currency: str = "USD") -> str:
        month = month or datetime.utcnow().strftime("%Y-%m")
        budget_id = f"budget-{month}"
        existing = self._fetchone("SELECT id FROM budget WHERE id = ?", (budget_id,))
        now = _utcnow()
        if existing:
            self._execute("UPDATE budget SET updated_at = ? WHERE id = ?", (now, budget_id))
            return budget_id
        self._execute(
            "INSERT INTO budget (id, name, month, currency, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (budget_id, name or f"Budget {month}", month, currency, now, now),
        )
        return budget_id

    def _ensure_group(self, budget_id: str, name: str) -> str:
        row = self._fetchone(
            "SELECT id FROM category_group WHERE budget_id = ? AND lower(name) = lower(?)",
            (budget_id, name),
        )
        if row:
            return row["id"]
        gid = f"group-{uuid.uuid4().hex[:8]}"
        ord_row = self._fetchone(
            "SELECT COALESCE(MAX(ord), 0) + 1 AS next_ord FROM category_group WHERE budget_id = ?",
            (budget_id,),
        )
        ord_val = int(ord_row["next_ord"]) if ord_row else 1
        now = _utcnow()
        self._execute(
            "INSERT INTO category_group (id, budget_id, name, ord, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (gid, budget_id, name, ord_val, now, now),
        )
        return gid

    def _ensure_category(self, group_id: str, name: str) -> str:
        row = self._fetchone(
            "SELECT id FROM category WHERE group_id = ? AND lower(name) = lower(?)",
            (group_id, name),
        )
        if row:
            return row["id"]
        cid = f"cat-{uuid.uuid4().hex[:8]}"
        now = _utcnow()
        self._execute(
            """
            INSERT INTO category (id, group_id, name, assigned, activity, available, target_type, target_value, due_date, created_at, updated_at)
            VALUES (?, ?, ?, 0, 0, 0, NULL, NULL, NULL, ?, ?)
            """,
            (cid, group_id, name, now, now),
        )
        return cid

    def _recalculate_category_totals(self, month: str) -> None:
        month = month or datetime.utcnow().strftime("%Y-%m")
        rows = self._fetchall(
            """
            SELECT c.id AS category_id,
                   COALESCE(SUM(t.amount), 0) AS activity_total
            FROM category c
            LEFT JOIN budget_transaction t ON t.category_id = c.id AND t.month = ?
            GROUP BY c.id
            """,
            (month,),
        )
        for row in rows:
            self._execute(
                "UPDATE category SET activity = ?, available = assigned + ? WHERE id = ?",
                (row["activity_total"], row["activity_total"], row["category_id"]),
            )

    def import_everydollar_csv(self, file_path: str, month: Optional[str] = None) -> Dict[str, Any]:
        month_value = month
        with open(file_path, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = [h for h in (reader.fieldnames or []) if h]
            normalized_fields = {_normalize_header(h) for h in fieldnames}
            required = {"group", "item", "type", "date", "merchant", "amount"}
            missing = sorted(required.difference(normalized_fields))
            if missing:
                return {"ok": False, "error": "missing_columns", "missing_columns": missing}
            rows = [row for row in reader if any((row or {}).values())]

        if not rows:
            return {"ok": False, "error": "empty_csv"}

        if not month_value:
            sample_date = rows[0].get("Date") or rows[0].get("date") or ""
            month_value = _month_from_date(sample_date)

        budget_id = self.upsert_budget(month_value)
        batch_id = f"batch-{uuid.uuid4().hex[:8]}"
        batch_hash = _hash_row([month_value, str(len(rows)), "everydollar"])
        self._execute(
            "INSERT INTO import_batch (id, source, file_name, month, imported_at, hash) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, "everydollar_csv", Path(file_path).name, month_value, _utcnow(), batch_hash),
        )

        inserted = 0
        duplicates = 0
        for row in rows:
            group = (row.get("Group") or row.get("group") or "Uncategorized").strip()
            item = (row.get("Item") or row.get("item") or row.get("Category") or "Uncategorized").strip()
            tx_type = (row.get("Type") or row.get("type") or "").lower()
            date = _normalize_date(row.get("Date") or row.get("date") or "")
            payee = (row.get("Merchant") or row.get("merchant") or row.get("Payee") or "").strip() or "Unknown"
            amount_raw = row.get("Amount") or row.get("amount") or "0"
            try:
                amount = float(str(amount_raw).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
            except ValueError:
                amount = 0.0
            if "income" in tx_type:
                amount = abs(amount)
            elif "expense" in tx_type or "spend" in tx_type:
                amount = -abs(amount)
            elif amount > 0:
                amount = -amount

            group_id = self._ensure_group(budget_id, group)
            category_id = self._ensure_category(group_id, item)
            tx_hash = _hash_row([date, payee, str(amount), item, month_value])
            if self._fetchone("SELECT id FROM budget_transaction WHERE hash = ?", (tx_hash,)):
                duplicates += 1
                continue
            tx_id = f"tx-{uuid.uuid4().hex[:8]}"
            self._execute(
                """
                INSERT INTO budget_transaction
                (id, date, month, payee, amount, category_id, account_id, memo, split_parent_id, source, import_batch_id, hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (tx_id, date, month_value, payee, amount, category_id, "everydollar_csv", batch_id, tx_hash, _utcnow()),
            )
            inserted += 1

        self._recalculate_category_totals(month_value)
        attempted = len(rows)
        return {
            "ok": True,
            "imported": inserted,
            "inserted_rows": inserted,
            "duplicate_rows": duplicates,
            "attempted_rows": attempted,
            "month": month_value,
            "batch_id": batch_id,
        }

    def import_excel_rows(self, rows: List[Dict[str, Any]], mapping: Dict[str, str], month: Optional[str] = None) -> Dict[str, Any]:
        if not rows:
            return {"ok": False, "error": "empty_rows"}
        resolved_mapping = dict(mapping or {})
        if not resolved_mapping:
            resolved_mapping = infer_excel_mapping(rows[0].keys())
        required_fields = ("date", "payee", "amount")
        missing_fields = [field for field in required_fields if not resolved_mapping.get(field)]
        if missing_fields:
            return {
                "ok": False,
                "error": "ambiguous_mapping",
                "missing_fields": missing_fields,
                "inferred_mapping": resolved_mapping,
            }

        month_value = month or datetime.utcnow().strftime("%Y-%m")
        budget_id = self.upsert_budget(month_value)
        batch_id = f"batch-{uuid.uuid4().hex[:8]}"
        batch_hash = _hash_row([month_value, str(len(rows)), "excel"])
        self._execute(
            "INSERT INTO import_batch (id, source, file_name, month, imported_at, hash) VALUES (?, ?, ?, ?, ?, ?)",
            (batch_id, "excel", "upload.xlsx", month_value, _utcnow(), batch_hash),
        )
        inserted = 0
        duplicates = 0
        for row in rows:
            date = _normalize_date(str(row.get(resolved_mapping.get("date", ""), "")))
            payee = str(row.get(resolved_mapping.get("payee", ""), "") or row.get(resolved_mapping.get("merchant", ""), "")).strip()
            amount_raw = row.get(resolved_mapping.get("amount", ""), 0)
            category_name = str(row.get(resolved_mapping.get("category", ""), "")).strip() or "Uncategorized"
            group_name = str(row.get(resolved_mapping.get("group", ""), "")).strip() or "General"
            memo = str(row.get(resolved_mapping.get("memo", ""), "")).strip()
            try:
                amount = float(str(amount_raw).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
            except ValueError:
                amount = 0.0
            tx_type = str(row.get(resolved_mapping.get("type", ""), "")).lower()
            if "income" in tx_type:
                amount = abs(amount)
            elif "expense" in tx_type and amount > 0:
                amount = -amount

            group_id = self._ensure_group(budget_id, group_name)
            category_id = self._ensure_category(group_id, category_name)
            tx_hash = _hash_row([date, payee, str(amount), category_name, month_value])
            if self._fetchone("SELECT id FROM budget_transaction WHERE hash = ?", (tx_hash,)):
                duplicates += 1
                continue
            tx_id = f"tx-{uuid.uuid4().hex[:8]}"
            self._execute(
                """
                INSERT INTO budget_transaction
                (id, date, month, payee, amount, category_id, account_id, memo, split_parent_id, source, import_batch_id, hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)
                """,
                (tx_id, date, month_value, payee or "Unknown", amount, category_id, memo or None, "excel", batch_id, tx_hash, _utcnow()),
            )
            inserted += 1

        self._recalculate_category_totals(month_value)
        attempted = len(rows)
        return {
            "ok": True,
            "imported": inserted,
            "inserted_rows": inserted,
            "duplicate_rows": duplicates,
            "attempted_rows": attempted,
            "month": month_value,
            "batch_id": batch_id,
            "mapping": resolved_mapping,
        }

    def add_transaction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        month_value = data.get("month") or _month_from_date(data.get("date", ""))
        budget_id = self.upsert_budget(month_value)
        category_name = data.get("category") or "Uncategorized"
        group_name = data.get("group") or "General"
        group_id = self._ensure_group(budget_id, group_name)
        category_id = self._ensure_category(group_id, category_name)

        date = _normalize_date(data.get("date") or "")
        payee = data.get("payee") or "Unknown"
        amount = float(data.get("amount") or 0)
        memo = data.get("memo")
        account_id = data.get("account_id")
        source = data.get("source") or "manual"
        split_parent_id = data.get("split_parent_id")

        tx_hash = _hash_row([date, payee, str(amount), category_name, month_value, source])
        tx_id = data.get("id") or f"tx-{uuid.uuid4().hex[:8]}"
        self._execute(
            """
            INSERT OR IGNORE INTO budget_transaction
            (id, date, month, payee, amount, category_id, account_id, memo, split_parent_id, source, import_batch_id, hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
            """,
            (tx_id, date, month_value, payee, amount, category_id, account_id, memo, split_parent_id, source, tx_hash, _utcnow()),
        )

        splits = data.get("splits") or []
        child_ids: List[str] = []
        if splits:
            for split in splits:
                child = dict(split)
                child["split_parent_id"] = tx_id
                child["date"] = date
                child["source"] = source
                child["month"] = month_value
                result = self.add_transaction(child)
                if result.get("id"):
                    child_ids.append(result["id"])
        self._recalculate_category_totals(month_value)
        return {"ok": True, "id": tx_id, "split_children": child_ids}

    def add_or_update_category(self, month: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        budget_id = self.upsert_budget(month)
        group_name = payload.get("group") or "General"
        group_id = self._ensure_group(budget_id, group_name)
        name = payload.get("name") or "New Category"
        category_id = payload.get("id") or self._ensure_category(group_id, name)

        assigned = payload.get("assigned")
        due_date = payload.get("due_date")
        target_type = payload.get("target_type")
        target_value = payload.get("target_value")
        now = _utcnow()

        row = self._fetchone("SELECT id FROM category WHERE id = ?", (category_id,))
        if row:
            if assigned is not None:
                self._execute(
                    "UPDATE category SET assigned = ?, due_date = ?, target_type = ?, target_value = ?, updated_at = ? WHERE id = ?",
                    (assigned, due_date, target_type, target_value, now, category_id),
                )
            else:
                self._execute(
                    "UPDATE category SET due_date = ?, target_type = ?, target_value = ?, updated_at = ? WHERE id = ?",
                    (due_date, target_type, target_value, now, category_id),
                )
        else:
            self._execute(
                """
                INSERT INTO category (id, group_id, name, assigned, activity, available, target_type, target_value, due_date, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                """,
                (category_id, group_id, name, assigned or 0, assigned or 0, target_type, target_value, due_date, now, now),
            )
        self._recalculate_category_totals(month)
        return {"ok": True, "id": category_id}

    def add_or_update_account(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        acc_id = payload.get("id") or f"acct-{uuid.uuid4().hex[:8]}"
        name = payload.get("name") or "Account"
        acc_type = payload.get("type") or payload.get("account_type") or ""
        balance = float(payload.get("balance") or 0)
        institution = payload.get("institution")
        now = _utcnow()
        existing = self._fetchone("SELECT id FROM account WHERE id = ?", (acc_id,))
        if existing:
            self._execute(
                "UPDATE account SET name = ?, type = ?, balance = ?, institution = ?, updated_at = ? WHERE id = ?",
                (name, acc_type, balance, institution, now, acc_id),
            )
        else:
            self._execute(
                "INSERT INTO account (id, name, type, balance, institution, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (acc_id, name, acc_type, balance, institution, now, now),
            )
        return {"ok": True, "id": acc_id}

    def get_accounts(self) -> Dict[str, Any]:
        rows = self._fetchall("SELECT * FROM account ORDER BY name")
        return {"ok": True, "accounts": [dict(r) for r in rows]}

    def get_categories(self, month: str) -> Dict[str, Any]:
        budget_id = self.upsert_budget(month)
        groups = self._fetchall(
            "SELECT id, name, ord FROM category_group WHERE budget_id = ? ORDER BY ord",
            (budget_id,),
        )
        categories = self._fetchall(
            "SELECT * FROM category WHERE group_id IN (SELECT id FROM category_group WHERE budget_id = ?)",
            (budget_id,),
        )
        group_map: Dict[str, Dict[str, Any]] = {}
        for g in groups:
            group_map[g["id"]] = {"id": g["id"], "name": g["name"], "ord": g["ord"], "categories": []}
        for cat in categories:
            group_map[cat["group_id"]]["categories"].append(dict(cat))
        return {"ok": True, "month": month, "groups": list(group_map.values())}

    def get_transactions(self, month: str) -> Dict[str, Any]:
        rows = self._fetchall(
            "SELECT t.*, c.name as category_name FROM budget_transaction t LEFT JOIN category c ON c.id = t.category_id WHERE t.month = ? ORDER BY date DESC",
            (month,),
        )
        return {"ok": True, "month": month, "transactions": [dict(r) for r in rows]}

    def get_summary(self, month: str) -> Dict[str, Any]:
        month_value = month or datetime.utcnow().strftime("%Y-%m")
        self._recalculate_category_totals(month_value)
        categories = self._fetchall(
            "SELECT assigned, activity, available, due_date, name FROM category WHERE group_id IN (SELECT id FROM category_group WHERE budget_id = ?)",
            (f"budget-{month_value}",),
        )
        assigned_total = sum(r["assigned"] or 0 for r in categories)
        activity_total = sum(r["activity"] or 0 for r in categories)
        available_total = sum(r["available"] or 0 for r in categories)
        overspent = [r for r in categories if (r["available"] or 0) < 0]
        due_soon = [r for r in categories if r["due_date"]]
        income = sum(r["amount"] for r in self._fetchall("SELECT amount FROM budget_transaction WHERE month = ? AND amount > 0", (month_value,)))
        expense = sum(r["amount"] for r in self._fetchall("SELECT amount FROM budget_transaction WHERE month = ? AND amount < 0", (month_value,)))
        net = income + expense
        return {
            "ok": True,
            "month": month_value,
            "assigned_total": assigned_total,
            "activity_total": activity_total,
            "available_total": available_total,
            "overspent_count": len(overspent),
            "income_total": income,
            "expense_total": expense,
            "net": net,
            "due_count": len(due_soon),
        }

    def add_paycheck(self, month: str, data: Dict[str, Any]) -> Dict[str, Any]:
        budget_id = self.upsert_budget(month)
        pid = data.get("id") or f"pay-{uuid.uuid4().hex[:8]}"
        self._execute(
            "INSERT INTO paycheck (id, budget_id, date, amount, source, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (pid, budget_id, _normalize_date(data.get("date") or ""), float(data.get("amount") or 0), data.get("source"), _utcnow()),
        )
        return {"ok": True, "id": pid}

    def get_paychecks(self, month: str) -> Dict[str, Any]:
        rows = self._fetchall(
            "SELECT * FROM paycheck WHERE budget_id = ? ORDER BY date",
            (f"budget-{month}",),
        )
        return {"ok": True, "month": month, "paychecks": [dict(r) for r in rows]}

    def delete_paycheck(self, paycheck_id: str) -> Dict[str, Any]:
        self._execute("DELETE FROM paycheck WHERE id = ?", (paycheck_id,))
        return {"ok": True, "id": paycheck_id}

    def add_goal(self, data: Dict[str, Any]) -> Dict[str, Any]:
        gid = data.get("id") or f"goal-{uuid.uuid4().hex[:8]}"
        now = _utcnow()
        self._execute(
            "INSERT INTO goal (id, name, target_amount, saved_amount, due_date, category_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (gid, data.get("name"), float(data.get("target_amount") or 0), float(data.get("saved_amount") or 0), data.get("due_date"), data.get("category_id"), now, now),
        )
        return {"ok": True, "id": gid}

    def update_goal(self, goal_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        now = _utcnow()
        self._execute(
            "UPDATE goal SET name = ?, target_amount = ?, saved_amount = ?, due_date = ?, category_id = ?, updated_at = ? WHERE id = ?",
            (
                data.get("name"),
                float(data.get("target_amount") or 0),
                float(data.get("saved_amount") or 0),
                data.get("due_date"),
                data.get("category_id"),
                now,
                goal_id,
            ),
        )
        return {"ok": True, "id": goal_id}

    def get_goals(self) -> Dict[str, Any]:
        rows = self._fetchall("SELECT * FROM goal ORDER BY created_at DESC")
        return {"ok": True, "goals": [dict(r) for r in rows]}

    def store_insight(self, month: str, summary: str, sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        iid = f"ins-{uuid.uuid4().hex[:8]}"
        self._execute(
            "INSERT INTO insight (id, month, summary, sources_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (iid, month, summary, json.dumps(sources), _utcnow()),
        )
        return {"ok": True, "id": iid}

    def latest_insight(self, month: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            "SELECT * FROM insight WHERE month = ? ORDER BY created_at DESC LIMIT 1",
            (month,),
        )
        return dict(row) if row else None

    def export_csv(self, month: str) -> str:
        rows = self._fetchall(
            "SELECT t.date, t.payee, t.amount, c.name as category, t.memo FROM budget_transaction t LEFT JOIN category c ON c.id = t.category_id WHERE t.month = ? ORDER BY t.date",
            (month,),
        )
        output = ["Date,Payee,Amount,Category,Memo"]
        for row in rows:
            output.append(
                f"{row['date']},{row['payee']},{row['amount']},{row['category'] or ''},{(row['memo'] or '').replace(',', ' ')}"
            )
        return "\n".join(output)


_STORE: Optional[BudgetStore] = None


def get_store(db_path: Optional[Path] = None) -> BudgetStore:
    global _STORE
    if _STORE is None:
        base = Path(__file__).resolve().parents[1]
        path = db_path or (base / "memory" / "budget.db")
        _STORE = BudgetStore(db_path=path)
        _STORE.ensure_schema()
    return _STORE
