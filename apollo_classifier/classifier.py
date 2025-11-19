"""
classifier.py - Financial Intent Classifier for Apollo

Multi-label keyword scoring with confidence levels.
"""

from typing import Dict, Any, List, Tuple
import re

# Intent types
INTENT_TYPES = ["markets", "personal_finance", "risk", "tax", "investing", "macro"]

# Keyword dictionaries with weights
INTENT_KEYWORDS = {
    "markets": {
        "keywords": [
            ("stock", 3), ("market", 3), ("price", 2), ("ticker", 3), ("shares", 3),
            ("crypto", 3), ("bitcoin", 3), ("forex", 3), ("index", 2), ("s&p", 3),
            ("nasdaq", 3), ("dow", 3), ("trading", 3), ("bull", 2), ("bear", 2),
            ("etf", 2), ("options", 3), ("futures", 3), ("commodities", 2),
            ("gold", 2), ("oil", 2), ("earnings", 2), ("ipo", 3)
        ],
        "boost_phrases": [
            "stock price", "market today", "buy stock", "sell stock",
            "what's happening with", "how is the market"
        ]
    },
    "personal_finance": {
        "keywords": [
            ("budget", 3), ("savings", 3), ("debt", 3), ("loan", 3), ("credit", 3),
            ("mortgage", 3), ("401k", 3), ("ira", 3), ("emergency fund", 3),
            ("spending", 2), ("income", 2), ("paycheck", 2), ("bills", 2),
            ("credit card", 3), ("student loan", 3), ("car payment", 2),
            ("save money", 3), ("expenses", 2), ("cash flow", 2)
        ],
        "boost_phrases": [
            "how much should i save", "pay off debt", "build savings",
            "my budget", "my income", "my expenses"
        ]
    },
    "risk": {
        "keywords": [
            ("risk", 4), ("volatility", 3), ("hedge", 3), ("diversify", 3),
            ("exposure", 3), ("downside", 3), ("var", 3), ("drawdown", 3),
            ("beta", 3), ("sharpe", 3), ("correlation", 2), ("concentration", 2),
            ("black swan", 3), ("tail risk", 3), ("stress test", 3)
        ],
        "boost_phrases": [
            "too much risk", "risk level", "how risky", "protect against",
            "reduce risk", "my risk", "risk tolerance"
        ]
    },
    "tax": {
        "keywords": [
            ("tax", 4), ("deduction", 3), ("irs", 3), ("capital gains", 4),
            ("w-2", 3), ("1099", 3), ("filing", 2), ("refund", 2),
            ("withholding", 2), ("bracket", 3), ("taxable", 3), ("write-off", 3),
            ("roth", 3), ("traditional", 2), ("backdoor", 3), ("tax-loss", 3),
            ("fica", 2), ("amt", 3), ("state tax", 2)
        ],
        "boost_phrases": [
            "how much tax", "reduce taxes", "tax implications", "tax bill",
            "tax efficient", "after-tax", "pre-tax"
        ]
    },
    "investing": {
        "keywords": [
            ("invest", 4), ("portfolio", 4), ("allocation", 3), ("dividend", 3),
            ("mutual fund", 3), ("bond", 3), ("yield", 2), ("return", 2),
            ("compound", 3), ("growth", 2), ("value", 2), ("rebalance", 3),
            ("dollar cost", 3), ("lump sum", 3), ("asset class", 3)
        ],
        "boost_phrases": [
            "should i invest", "how to invest", "investment strategy",
            "grow my money", "where to put", "best investment"
        ]
    },
    "macro": {
        "keywords": [
            ("gdp", 4), ("inflation", 4), ("fed", 3), ("interest rate", 4),
            ("unemployment", 3), ("recession", 4), ("cpi", 3), ("ppi", 3),
            ("monetary", 3), ("fiscal", 3), ("federal reserve", 4),
            ("economy", 3), ("economic", 3), ("fomc", 3), ("yield curve", 3),
            ("inverted", 2), ("basis points", 3)
        ],
        "boost_phrases": [
            "what's the fed", "interest rates going", "economy heading",
            "inflation rate", "recession coming", "economic outlook"
        ]
    }
}

# Override rules for specific patterns
OVERRIDE_RULES = [
    (r'\b401k\b|\bira\b|\broth\b', "personal_finance"),
    (r'\bcrypto|bitcoin|ethereum\b', "markets"),
    (r'\bfed\b.*\brate', "macro"),
    (r'\btax.*return|file.*tax', "tax"),
    (r'\bsharpe.*ratio|beta\b', "risk"),
]


class FinancialClassifier:
    """
    Multi-label financial intent classifier with confidence scoring.
    """

    def __init__(self):
        self.intents = INTENT_KEYWORDS
        self.override_rules = OVERRIDE_RULES

    def classify(self, message: str) -> Dict[str, Any]:
        """
        Classify a message into financial intents.

        Args:
            message: User message to classify

        Returns:
            Classification result with intents and confidence
        """
        message_lower = message.lower()

        # Calculate scores for each intent
        scores = {}
        matched_keywords = {}

        for intent, data in self.intents.items():
            score = 0
            matches = []

            # Keyword matching
            for keyword, weight in data["keywords"]:
                if keyword in message_lower:
                    score += weight
                    matches.append(keyword)

            # Phrase boost
            for phrase in data.get("boost_phrases", []):
                if phrase in message_lower:
                    score += 5
                    matches.append(f"[{phrase}]")

            scores[intent] = score
            matched_keywords[intent] = matches

        # Apply override rules
        primary_override = None
        for pattern, intent in self.override_rules:
            if re.search(pattern, message_lower):
                primary_override = intent
                scores[intent] += 10
                break

        # Determine primary intent
        max_score = max(scores.values()) if scores else 0
        if max_score == 0:
            primary_intent = "unknown"
            confidence = 0.0
        else:
            primary_intent = max(scores, key=scores.get)
            # Normalize confidence (0-1)
            confidence = min(1.0, max_score / 20)

        # Get secondary intents
        sorted_intents = sorted(scores.items(), key=lambda x: -x[1])
        secondary_intents = [
            intent for intent, score in sorted_intents[1:4]
            if score > 0
        ]

        # Multi-label detection
        multi_label = []
        threshold = max_score * 0.6 if max_score > 0 else 0
        for intent, score in scores.items():
            if score >= threshold and score > 0:
                multi_label.append(intent)

        return {
            "primary_intent": primary_intent,
            "confidence": round(confidence, 3),
            "all_scores": scores,
            "matched_keywords": matched_keywords.get(primary_intent, []),
            "secondary_intents": secondary_intents,
            "multi_label": multi_label,
            "override_applied": primary_override,
        }

    def get_confidence_level(self, confidence: float) -> str:
        """Convert confidence score to level."""
        if confidence >= 0.8:
            return "high"
        elif confidence >= 0.5:
            return "medium"
        elif confidence >= 0.2:
            return "low"
        else:
            return "very_low"


def classify_message(message: str) -> Dict[str, Any]:
    """
    Convenience function to classify a message.

    Args:
        message: Message to classify

    Returns:
        Classification result
    """
    classifier = FinancialClassifier()
    result = classifier.classify(message)
    result["confidence_level"] = classifier.get_confidence_level(result["confidence"])
    return result


# Test messages with expected classifications
TEST_MESSAGES = [
    ("What's the price of Apple stock today?", "markets"),
    ("How should I budget for rent?", "personal_finance"),
    ("Is my portfolio too risky?", "risk"),
    ("How much tax will I owe on capital gains?", "tax"),
    ("Where should I invest $10,000?", "investing"),
    ("What's the Fed doing with interest rates?", "macro"),
    ("Should I rebalance my 401k?", "personal_finance"),
    ("How does inflation affect my savings?", "macro"),
    ("What's my Sharpe ratio?", "risk"),
    ("Can I deduct my home office on taxes?", "tax"),
]


if __name__ == "__main__":
    print("Testing Financial Intent Classifier")
    print("=" * 70)

    classifier = FinancialClassifier()
    correct = 0

    for message, expected in TEST_MESSAGES:
        result = classify_message(message)
        actual = result["primary_intent"]
        match = "✓" if actual == expected else "✗"

        if actual == expected:
            correct += 1

        print(f"\n{match} Message: {message}")
        print(f"  Expected: {expected}, Got: {actual}")
        print(f"  Confidence: {result['confidence']:.2f} ({result['confidence_level']})")
        print(f"  Keywords: {', '.join(result['matched_keywords'][:5])}")
        if result["multi_label"] and len(result["multi_label"]) > 1:
            print(f"  Multi-label: {result['multi_label']}")

    print("\n" + "=" * 70)
    print(f"Accuracy: {correct}/{len(TEST_MESSAGES)} ({correct/len(TEST_MESSAGES)*100:.0f}%)")
