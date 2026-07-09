from __future__ import annotations

from pathlib import Path

from apollo_budget.store import BudgetStore


def _write_everydollar_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Group,Item,Type,Date,Merchant,Amount",
                "Essentials,Groceries,Expense,2026-04-01,Whole Foods,125.50",
                "Essentials,Fuel,Expense,2026-04-02,Shell,40.25",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def test_everydollar_import_reports_duplicate_rows(tmp_path):
    db_path = tmp_path / "budget.db"
    csv_path = tmp_path / "everydollar.csv"
    _write_everydollar_csv(csv_path)

    store = BudgetStore(db_path=db_path)
    store.ensure_schema()

    first = store.import_everydollar_csv(str(csv_path), month="2026-04")
    second = store.import_everydollar_csv(str(csv_path), month="2026-04")

    assert first["ok"] is True
    assert first["attempted_rows"] == 2
    assert first["inserted_rows"] == 2
    assert first["duplicate_rows"] == 0

    assert second["ok"] is True
    assert second["attempted_rows"] == 2
    assert second["inserted_rows"] == 0
    assert second["duplicate_rows"] == 2
