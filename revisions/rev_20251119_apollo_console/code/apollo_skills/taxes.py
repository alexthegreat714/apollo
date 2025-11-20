"""
taxes.py - Tax analysis skill for Apollo.

Provides capital gains estimation, tax-loss harvesting, and wash-sale checking.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class TaxSkill:
    """
    Tax analysis and planning skill.
    """

    def __init__(self):
        """Initialize the tax skill."""
        self.name = "taxes"
        self.description = "Capital gains, tax-loss harvesting, wash-sale rules"

        # 2024 tax brackets (simplified)
        self.long_term_rates = {
            "0": 0,
            "15": 0.15,
            "20": 0.20
        }

    def analyze(
        self,
        query: str,
        context: str = "",
        user_data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Analyze a tax-related query.

        Args:
            query: User query
            context: RAG context
            user_data: User tax data

        Returns:
            Analysis result
        """
        user_data = user_data or {}
        result = {
            "skill": self.name,
            "analysis": {},
            "recommendations": [],
            "context_used": bool(context)
        }

        query_lower = query.lower()

        if "capital gain" in query_lower:
            result["analysis"]["capital_gains"] = self.estimate_capital_gains(user_data)

        if "tax-loss" in query_lower or "tax loss" in query_lower or "harvest" in query_lower:
            result["analysis"]["tax_loss_harvesting"] = self.analyze_tax_loss_harvesting(user_data)

        if "wash" in query_lower:
            result["analysis"]["wash_sale"] = self.check_wash_sale(user_data)

        result["recommendations"] = self._generate_recommendations(result["analysis"])

        return result

    def estimate_capital_gains(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Estimate capital gains tax.

        Args:
            data: Transaction data

        Returns:
            Capital gains estimate
        """
        transactions = data.get("transactions", [])
        income = data.get("gross_income", 100000)

        if not transactions:
            return {
                "note": "Provide transactions list with cost_basis, sale_price, holding_days"
            }

        short_term_gains = 0
        long_term_gains = 0
        short_term_losses = 0
        long_term_losses = 0

        for tx in transactions:
            cost = tx.get("cost_basis", 0)
            sale = tx.get("sale_price", 0)
            days = tx.get("holding_days", 0)

            gain = sale - cost

            if days > 365:
                if gain > 0:
                    long_term_gains += gain
                else:
                    long_term_losses += abs(gain)
            else:
                if gain > 0:
                    short_term_gains += gain
                else:
                    short_term_losses += abs(gain)

        # Net gains after losses
        net_short = short_term_gains - short_term_losses
        net_long = long_term_gains - long_term_losses

        # Tax calculation (simplified)
        # Determine long-term rate based on income
        if income < 44625:
            lt_rate = 0
        elif income < 492300:
            lt_rate = 0.15
        else:
            lt_rate = 0.20

        # Short-term taxed as ordinary income (simplified 24%)
        st_rate = 0.24

        st_tax = max(0, net_short) * st_rate
        lt_tax = max(0, net_long) * lt_rate

        return {
            "short_term": {
                "gains": round(short_term_gains, 2),
                "losses": round(short_term_losses, 2),
                "net": round(net_short, 2),
                "tax_rate": f"{st_rate:.0%}",
                "estimated_tax": round(st_tax, 2)
            },
            "long_term": {
                "gains": round(long_term_gains, 2),
                "losses": round(long_term_losses, 2),
                "net": round(net_long, 2),
                "tax_rate": f"{lt_rate:.0%}",
                "estimated_tax": round(lt_tax, 2)
            },
            "total_estimated_tax": round(st_tax + lt_tax, 2)
        }

    def analyze_tax_loss_harvesting(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze tax-loss harvesting opportunities.

        Args:
            data: Portfolio data with unrealized gains/losses

        Returns:
            Tax-loss harvesting opportunities
        """
        positions = data.get("positions", [])

        if not positions:
            return {
                "note": "Provide positions list with symbol, cost_basis, current_value"
            }

        opportunities = []
        total_unrealized_loss = 0

        for pos in positions:
            symbol = pos.get("symbol", "Unknown")
            cost = pos.get("cost_basis", 0)
            current = pos.get("current_value", 0)
            unrealized = current - cost

            if unrealized < 0:
                loss = abs(unrealized)
                total_unrealized_loss += loss

                # Estimate tax savings (24% bracket)
                tax_savings = loss * 0.24

                opportunities.append({
                    "symbol": symbol,
                    "unrealized_loss": round(loss, 2),
                    "potential_tax_savings": round(tax_savings, 2),
                    "cost_basis": round(cost, 2),
                    "current_value": round(current, 2)
                })

        # Sort by loss amount
        opportunities.sort(key=lambda x: x["unrealized_loss"], reverse=True)

        return {
            "opportunities": opportunities,
            "total_unrealized_losses": round(total_unrealized_loss, 2),
            "max_deduction_per_year": 3000,
            "note": "Losses above $3,000 can be carried forward to future years"
        }

    def check_wash_sale(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check for wash sale rule violations.

        Args:
            data: Transaction history

        Returns:
            Wash sale analysis
        """
        transactions = data.get("transactions", [])

        if not transactions:
            return {
                "note": "Provide transactions list with symbol, date, action (buy/sell)"
            }

        # Sort by date
        sorted_tx = sorted(transactions, key=lambda x: x.get("date", ""))

        violations = []

        for i, sell in enumerate(sorted_tx):
            if sell.get("action") != "sell":
                continue

            symbol = sell.get("symbol")
            sell_date = datetime.fromisoformat(sell.get("date", "2024-01-01"))

            # Check 30 days before and after
            window_start = sell_date - timedelta(days=30)
            window_end = sell_date + timedelta(days=30)

            for buy in sorted_tx:
                if buy.get("action") != "buy":
                    continue
                if buy.get("symbol") != symbol:
                    continue

                buy_date = datetime.fromisoformat(buy.get("date", "2024-01-01"))

                if window_start <= buy_date <= window_end and buy_date != sell_date:
                    violations.append({
                        "symbol": symbol,
                        "sell_date": sell.get("date"),
                        "buy_date": buy.get("date"),
                        "days_apart": abs((buy_date - sell_date).days)
                    })

        return {
            "violations_found": len(violations),
            "violations": violations,
            "rule": "Cannot claim loss if substantially identical security purchased within 30 days before or after sale"
        }

    def _generate_recommendations(self, analysis: Dict[str, Any]) -> List[str]:
        """Generate tax recommendations."""
        recommendations = []

        # Tax-loss harvesting recommendations
        tlh = analysis.get("tax_loss_harvesting", {})
        if tlh.get("opportunities"):
            total = tlh.get("total_unrealized_losses", 0)
            recommendations.append(
                f"${total:,.0f} in unrealized losses available for tax-loss harvesting"
            )

        # Wash sale warnings
        ws = analysis.get("wash_sale", {})
        if ws.get("violations_found", 0) > 0:
            recommendations.append(
                f"Warning: {ws['violations_found']} potential wash sale violations detected"
            )

        # Capital gains recommendations
        cg = analysis.get("capital_gains", {})
        if cg.get("total_estimated_tax", 0) > 1000:
            recommendations.append(
                "Consider offsetting gains with tax-loss harvesting before year end"
            )

        return recommendations
