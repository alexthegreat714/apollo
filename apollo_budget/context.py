from __future__ import annotations

from typing import Any, Dict, List


def build_budget_context(store, month: str) -> str:
    summary = store.get_summary(month)
    if not summary or not summary.get("ok"):
        return ""

    categories = store.get_categories(month).get("groups", [])
    flat = []
    for group in categories:
        for cat in group.get("categories", []):
            flat.append(cat)

    overspent = [c for c in flat if (c.get("available") or 0) < 0]
    overspent.sort(key=lambda c: c.get("available") or 0)
    top_overspent = overspent[:3]

    due = [c for c in flat if c.get("due_date")]
    due.sort(key=lambda c: c.get("due_date") or "")
    due_soon = due[:3]

    tx = store.get_transactions(month).get("transactions", [])[:12]

    lines = [
        f"Budget summary ({month}): assigned={summary.get('assigned_total'):.2f}, "
        f"activity={summary.get('activity_total'):.2f}, available={summary.get('available_total'):.2f}, "
        f"overspent={summary.get('overspent_count')}, income={summary.get('income_total'):.2f}, "
        f"expense={summary.get('expense_total'):.2f}, net={summary.get('net'):.2f}."
    ]
    if top_overspent:
        lines.append(
            "Overspent categories: "
            + ", ".join(f"{c.get('name')} ({c.get('available'):.2f})" for c in top_overspent)
            + "."
        )
    if due_soon:
        lines.append(
            "Upcoming due categories: "
            + ", ".join(f"{c.get('name')} due {c.get('due_date')}" for c in due_soon)
            + "."
        )
    if tx:
        lines.append(
            "Recent transactions: "
            + "; ".join(
                f"{t.get('date')} {t.get('payee')} {t.get('amount'):.2f} ({t.get('category_name') or 'uncategorized'})"
                for t in tx
            )
            + "."
        )

    return "\n".join(lines)
