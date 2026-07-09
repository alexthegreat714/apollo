import json
import os
import time
from dataclasses import dataclass
import sys

from common.agent_manifest_schema import load_agent_manifest
from common.query_client import query_model_with_meta


def _apollo_llm_target() -> tuple[str, str]:
    try:
        manifest = load_agent_manifest("Apollo")
        primary = (manifest.get("ui_chat_policy") or {}).get("primary") or {}
        base_url = str(primary.get("base_url") or "").strip() or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
        model = str(primary.get("model") or "").strip() or "gemma4:31b"
        return base_url, model
    except Exception:
        return os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"), "gemma4:31b"


def _json_from_text(raw: str) -> dict:
    if not isinstance(raw, str):
        return {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    snippet = raw[start : end + 1]
    try:
        data = json.loads(snippet)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _grade_transcript(transcript: str) -> dict:
    base_url, model = _apollo_llm_target()
    prompt = (
        "Grade Apollo on a personal finance conversation. Score each 0-100:\n"
        "- depth\n"
        "- accuracy\n"
        "- context_awareness\n"
        "- tool_use (web search usage + incorporating results)\n"
        "- rag_grounding\n"
        "- safety\n"
        "- overall\n\n"
        "Return JSON only: {"
        "\"depth\":0,\"accuracy\":0,\"context_awareness\":0,\"tool_use\":0,"
        "\"rag_grounding\":0,\"safety\":0,\"overall\":0,\"notes\":\"\"}.\n\n"
        "Transcript:\n"
        + transcript[:9000]
    )
    meta = query_model_with_meta(prompt, task_type="qa", timeout_sec=180, retries=1, base_url=base_url, model=model)
    data = _json_from_text(str(meta.get("text") or ""))
    # sanitize
    out = {}
    for key in ("depth", "accuracy", "context_awareness", "tool_use", "rag_grounding", "safety", "overall"):
        try:
            out[key] = int(max(0, min(100, int(data.get(key) or 0))))
        except Exception:
            out[key] = 0
    out["notes"] = str(data.get("notes") or "").strip()
    return out


@dataclass
class _Turn:
    role: str
    text: str


def _run_eval(*, tools_enabled: bool, timeout_sec: int) -> dict:
    os.environ.setdefault("AGENT_NAME", "Apollo")
    os.environ.setdefault("SKY_AGENT_NAME", "Apollo")
    os.environ["APOLLO_ALLOW_CHAT_TOOLS"] = "1" if tools_enabled else "0"
    os.environ["APOLLO_CHAT_TIMEOUT_SECS"] = str(timeout_sec)
    os.environ.setdefault("APOLLO_CHAT_FAST_TIMEOUT_SECS", "45")
    os.environ.setdefault("APOLLO_CHAT_DEEP_TIMEOUT_SECS", "165")

    if tools_enabled:
        os.environ.setdefault("SKY_WEB_ENABLED", "1")
        os.environ.setdefault("SKY_WEB_PROVIDER", "duckduckgo_html")
        os.environ.setdefault("SKY_WEB_RATE_LIMIT_CALLS_PER_HOUR", "120")

    from Apollo.app import app  # import after env

    client = app.test_client()
    cid = f"eval_{'after' if tools_enabled else 'before'}_{int(time.time())}"

    def chat(message: str) -> str:
        resp = client.post("/chat", json={"conversation_id": cid, "message": message})
        js = resp.get_json() or {}
        return str(js.get("reply") or "")

    # Seed RAG with a small personal-finance anchor so we can test grounding.
    client.post(
        "/rag/write",
        json={
            "text": "Emergency fund guideline: keep 3–6 months of essential expenses in cash-equivalents before aggressive investing. Debt payoff: avalanche (highest APR first) minimizes total interest.",
            "source": "eval_seed",
            "kind": "pf_note",
            "tags": ["eval", "personal_finance"],
            "priority": 0.9,
        },
    )

    turns: list[_Turn] = []

    q1 = (
        "Personal finance: I net $6,000/mo after tax. Fixed expenses $3,800/mo. "
        "I have $18k credit card debt at ~22% APR, $8k emergency fund, and $12k in a Roth IRA. "
        "Create a 90-day plan (steps + monthly targets). Ask missing questions."
    )
    turns.append(_Turn("User", q1))
    turns.append(_Turn("Apollo", chat(q1)))

    q2 = (
        "Update: I can cut $400/mo spending and I also have a car loan $11k at 6.5% with $320/mo payment. "
        "Revise the plan and explain tradeoffs."
    )
    turns.append(_Turn("User", q2))
    turns.append(_Turn("Apollo", chat(q2)))

    if tools_enabled:
        q3 = "!web FINRA pattern day trader rule margin requirements"
        turns.append(_Turn("User", q3))
        turns.append(_Turn("Apollo", chat(q3)))

        q4 = "Using the web results you just pulled, give the key compliance/risk takeaways in 6 bullets."
        turns.append(_Turn("User", q4))
        turns.append(_Turn("Apollo", chat(q4)))

        q5 = "!rag emergency fund guideline avalanche method"
        turns.append(_Turn("User", q5))
        turns.append(_Turn("Apollo", chat(q5)))

    q6 = "Quick recall: what emergency fund target did you recommend and why? Use any pulled context if available."
    turns.append(_Turn("User", q6))
    turns.append(_Turn("Apollo", chat(q6)))

    transcript = "\n\n".join([f"{t.role}: {t.text}" for t in turns])
    grade = _grade_transcript(transcript)
    return {"conversation_id": cid, "timeout_sec": timeout_sec, "tools_enabled": tools_enabled, "grade": grade, "transcript": transcript}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    before = _run_eval(tools_enabled=False, timeout_sec=35)
    after = _run_eval(tools_enabled=True, timeout_sec=120)
    print(json.dumps({"before": before["grade"], "after": after["grade"]}, indent=2))
    print("\n--- BEFORE (truncated) ---\n")
    print(before["transcript"][:2500].encode("cp1252", "replace").decode("cp1252"))
    print("\n--- AFTER (truncated) ---\n")
    print(after["transcript"][:2500].encode("cp1252", "replace").decode("cp1252"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
