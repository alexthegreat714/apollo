from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock
from typing import Any, Dict

from Apollo.apollo_tools import aegis_vm_client


_APOLLO_ROOT = Path(__file__).resolve().parents[1]
_ENGINEERING_ROOT = _APOLLO_ROOT.parent
_STATE_DIR = _APOLLO_ROOT / "logs" / "student_loan_review"
_STATE_LOCK = Lock()
_CAPTURE_TARGETS = {"aegis_tab", "direct_console"}
_LOAN_TERMS = (
    "student loan",
    "student loans",
    "repayment plan",
    "income-driven",
    "save plan",
    "ibr",
    "icr",
    "paye",
    "repaye",
    "nelnet",
    "mohela",
    "aidvantage",
    "edfinancial",
    "federal student aid",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9_.-]+", "_", str(value or "").strip().lower())
    text = re.sub(r"_+", "_", text).strip("._-")
    return text[:80] or "default"


def _session_path(session_id: str) -> Path:
    return _STATE_DIR / f"{_slug(session_id)}.json"


def _default_session(session_id: str) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "session_id": str(session_id or "default"),
        "mode_active": True,
        "capture_paused": True,
        "pause_reason": "login_blind_mode",
        "target": "aegis_tab",
        "updated_at": now,
        "last_capture": {},
        "previous_capture": {},
    }


def _read_session_unlocked(session_id: str) -> Dict[str, Any]:
    path = _session_path(session_id)
    if not path.exists():
        return _default_session(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _default_session(session_id)
    if not isinstance(data, dict):
        return _default_session(session_id)
    session = _default_session(session_id)
    session.update(data)
    session["session_id"] = str(session_id or "default")
    if str(session.get("target") or "") not in _CAPTURE_TARGETS:
        session["target"] = "aegis_tab"
    return session


def _write_session_unlocked(session: Dict[str, Any]) -> Dict[str, Any]:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = _now_iso()
    _session_path(str(session.get("session_id") or "default")).write_text(
        json.dumps(session, indent=2),
        encoding="utf-8",
    )
    return session


def _redact_text(text: str) -> str:
    out = str(text or "")
    out = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "***-**-****", out)
    out = re.sub(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", r"\1***@\2", out)
    out = re.sub(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?){2}\d{4}\b", "***-***-****", out)
    out = re.sub(r"\b(\d{2})\d{4,}(\d{2})\b", r"\1****\2", out)
    out = re.sub(r"(?im)\b(account|loan|reference|id)\s*(number|no\.?|#)?\s*[:\-]?\s*([a-z0-9\-]{6,})", r"\1 \2: [redacted]", out)
    out = re.sub(r"(?im)\b(address|street|city|state|zip)\s*[:\-].*", r"\1: [redacted]", out)
    out = re.sub(r"(?im)\bpayment method\b.*", "payment method: [redacted]", out)
    return out.strip()


def resolve_artifact_path(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    allowed_roots = (
        (_ENGINEERING_ROOT / "Aegis" / "artifacts").resolve(),
        (_ENGINEERING_ROOT / "Aegis" / "logs").resolve(),
        (_ENGINEERING_ROOT / "Apollo" / "logs").resolve(),
        (_ENGINEERING_ROOT / "artifacts").resolve(),
    )
    raw = Path(str(raw_path or "").strip())
    candidates = []
    if raw.is_absolute():
        candidates.append(raw.resolve())
    else:
        candidates.append((_ENGINEERING_ROOT / raw).resolve())
        candidates.append((_ENGINEERING_ROOT / "Aegis" / raw).resolve())
    for path in candidates:
        if not any(str(path).lower().startswith(str(root).lower()) for root in allowed_roots):
            continue
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        return path
    return None


def _sample_lines(text: str, limit: int = 5) -> list[str]:
    lines: list[str] = []
    for raw in re.split(r"[\r\n]+", str(text or "")):
        line = re.sub(r"\s+", " ", raw).strip(" -:\t")
        if len(line) < 8:
            continue
        if line not in lines:
            lines.append(line[:180])
        if len(lines) >= limit:
            break
    if lines:
        return lines
    chunks = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", str(text or "")).strip())
    return [chunk[:180] for chunk in chunks if len(chunk.strip()) >= 8][:limit]


def _amounts(text: str) -> list[str]:
    seen: list[str] = []
    for match in re.findall(r"\$\s?\d[\d,]*(?:\.\d{2})?", str(text or "")):
        value = match.replace(" ", "")
        if value not in seen:
            seen.append(value)
        if len(seen) >= 4:
            break
    return seen


def _plan_names(text: str) -> list[str]:
    plans = []
    for token, label in (
        ("save", "SAVE"),
        ("ibr", "IBR"),
        ("icr", "ICR"),
        ("paye", "PAYE"),
        ("repaye", "REPAYE"),
        ("standard repayment", "Standard Repayment"),
        ("graduated", "Graduated Repayment"),
        ("extended", "Extended Repayment"),
    ):
        if token in str(text or "").lower() and label not in plans:
            plans.append(label)
    return plans


def _page_kind(text: str) -> str:
    low = str(text or "").lower()
    if any(token in low for token in ("sign in", "log in", "two-factor", "verification code", "security code", "passcode", "multifactor", "authenticate")):
        return "login or verification page"
    if any(token in low for token in ("save plan", "income-driven", "monthly payment", "forgiveness", "repaye", "paye", "ibr", "icr")):
        return "repayment-plan comparison or enrollment page"
    if any(token in low for token in ("loan details", "interest rate", "principal balance", "current balance", "disbursement")):
        return "loan details page"
    if any(token in low for token in ("loan simulator", "simulator", "estimate your payment", "repayment estimator")):
        return "repayment estimator or simulator"
    if any(token in low for token in ("payment history", "payment amount", "next payment", "autopay", "due date")):
        return "payment dashboard or account overview"
    if any(token in low for token in ("consolidation", "consolidate")):
        return "loan consolidation workflow"
    if any(token in low for token in ("deferment", "forbearance", "hardship")):
        return "deferment or hardship options page"
    if any(token in low for token in ("upload documentation", "income documentation", "family size", "recertify", "recertification")):
        return "income recertification workflow"
    if any(token in low for token in ("federal student aid", "studentaid.gov", "my aid")):
        return "Federal Student Aid account page"
    return "student-loan servicer page"


def _takeaways(text: str) -> list[str]:
    low = str(text or "").lower()
    notes: list[str] = []
    plans = _plan_names(text)
    amounts = _amounts(text)
    if "mohela" in low:
        notes.append("This appears to be a MOHELA-serviced page.")
    elif "nelnet" in low:
        notes.append("This appears to be a Nelnet-serviced page.")
    elif "aidvantage" in low:
        notes.append("This appears to be an Aidvantage-serviced page.")
    elif "edfinancial" in low:
        notes.append("This appears to be an EdFinancial-serviced page.")
    elif "studentaid.gov" in low or "federal student aid" in low:
        notes.append("This appears to be a Federal Student Aid page rather than the servicer site.")
    if plans:
        notes.append("Plans visible: " + ", ".join(plans[:4]) + ".")
    if amounts:
        notes.append("Dollar amounts on screen: " + ", ".join(amounts[:4]) + ".")
    if "estimated monthly payment" in low or "monthly payment" in low:
        notes.append("A payment estimate is visible and should be compared against total repayment horizon.")
    if "forgiveness" in low:
        notes.append("This page references forgiveness timing or eligibility.")
    if "recert" in low or "recertification" in low:
        notes.append("Income recertification appears relevant on this screen.")
    if "family size" in low or "agi" in low or "adjusted gross income" in low:
        notes.append("Income-driven payment inputs are visible, so AGI and family-size assumptions matter here.")
    if "autopay" in low or "auto pay" in low:
        notes.append("Autopay or scheduled payment settings are visible.")
    if "interest rate" in low or "unpaid interest" in low:
        notes.append("Interest treatment appears on-screen and should be compared across options.")
    if not notes:
        notes.append("Key comparison points are likely monthly payment, total paid over time, forgiveness path, and recertification burden.")
    return notes[:4]


def _compare_text(previous_text: str, current_text: str) -> Dict[str, Any]:
    prev = str(previous_text or "").strip()
    curr = str(current_text or "").strip()
    if not prev or not curr:
        return {"available": False, "summary": "No prior capture available for comparison."}
    prev_lines = _sample_lines(prev, limit=8)
    curr_lines = _sample_lines(curr, limit=8)
    prev_norm = {line.lower(): line for line in prev_lines}
    curr_norm = {line.lower(): line for line in curr_lines}
    new_lines = [curr_norm[key] for key in curr_norm.keys() if key not in prev_norm][:4]
    gone_lines = [prev_norm[key] for key in prev_norm.keys() if key not in curr_norm][:3]
    similarity = round(SequenceMatcher(None, prev[:3000], curr[:3000]).ratio(), 3)
    summary = "Screen is very similar to the prior capture." if similarity >= 0.9 else "Screen content changed from the prior capture."
    return {
        "available": True,
        "summary": summary,
        "similarity": similarity,
        "new_lines": new_lines,
        "gone_lines": gone_lines,
    }


def _public_capture(capture: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(capture, dict) or not capture:
        return {}
    return {
        "captured_at": str(capture.get("captured_at") or ""),
        "screenshot_path": str(capture.get("screenshot_path") or ""),
        "capture_target": str(capture.get("capture_target") or ""),
        "capture_source": str(capture.get("capture_source") or ""),
        "ocr_engine": str(capture.get("ocr_engine") or ""),
        "page_kind": str(capture.get("page_kind") or ""),
        "sample_lines": list(capture.get("sample_lines") or [])[:5],
        "takeaways": list(capture.get("takeaways") or [])[:4],
        "comparison": dict(capture.get("comparison") or {}),
    }


def _public_session(session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "session_id": str(session.get("session_id") or "default"),
        "mode_active": bool(session.get("mode_active")),
        "capture_paused": bool(session.get("capture_paused")),
        "pause_reason": str(session.get("pause_reason") or ""),
        "target": str(session.get("target") or "aegis_tab"),
        "updated_at": str(session.get("updated_at") or ""),
        "last_capture": _public_capture(session.get("last_capture") if isinstance(session.get("last_capture"), dict) else {}),
        "previous_capture": _public_capture(session.get("previous_capture") if isinstance(session.get("previous_capture"), dict) else {}),
    }


def get_session(session_id: str) -> Dict[str, Any]:
    with _STATE_LOCK:
        return _public_session(_read_session_unlocked(session_id))


def set_mode(
    session_id: str,
    *,
    mode_active: bool | None = None,
    capture_paused: bool | None = None,
    pause_reason: str | None = None,
    target: str | None = None,
) -> Dict[str, Any]:
    with _STATE_LOCK:
        session = _read_session_unlocked(session_id)
        if mode_active is not None:
            session["mode_active"] = bool(mode_active)
        if capture_paused is not None:
            session["capture_paused"] = bool(capture_paused)
        if pause_reason is not None:
            session["pause_reason"] = str(pause_reason or "")
        if target:
            target_value = str(target).strip().lower()
            if target_value in _CAPTURE_TARGETS:
                session["target"] = target_value
        _write_session_unlocked(session)
        return {"ok": True, "session": _public_session(session)}


def _build_reply(capture: Dict[str, Any], session: Dict[str, Any]) -> str:
    sample_lines = list(capture.get("sample_lines") or [])
    takeaways = list(capture.get("takeaways") or [])
    comparison = dict(capture.get("comparison") or {})
    lines = ["Apollo captured the VM screen for student-loan review."]
    lines.append("")
    lines.append("What I can read:")
    if sample_lines:
        for line in sample_lines[:4]:
            lines.append(f"- {line}")
    else:
        lines.append("- OCR did not recover enough text to quote reliably from this screen.")
    lines.append("")
    lines.append("What this page appears to be:")
    lines.append(f"- {capture.get('page_kind') or 'student-loan account page'}")
    lines.append("")
    lines.append("My take:")
    for item in takeaways[:4]:
        lines.append(f"- {item}")
    if comparison.get("available"):
        lines.append("")
        lines.append("What changed since the last capture:")
        lines.append(f"- {comparison.get('summary')}")
        for item in list(comparison.get("new_lines") or [])[:3]:
            lines.append(f"- New on screen: {item}")
    if bool(session.get("capture_paused")):
        lines.append("")
        lines.append(f"Capture guard: paused (`{session.get('pause_reason') or 'manual_pause'}`).")
    else:
        lines.append("")
        lines.append("Capture guard: active.")
    return "\n".join(lines).strip()


def capture_screen(session_id: str, *, message: str = "", compare_last: bool = False) -> Dict[str, Any]:
    with _STATE_LOCK:
        session = _read_session_unlocked(session_id)
    if not bool(session.get("mode_active")):
        return {"ok": False, "error": "loan_review_mode_inactive", "session": _public_session(session), "reply": "Student-loan review mode is off."}
    if bool(session.get("capture_paused")):
        return {
            "ok": False,
            "error": "capture_paused",
            "session": _public_session(session),
            "reply": f"Capture is paused for `{session.get('pause_reason') or 'manual_pause'}`. Resume capture when you are past login or MFA.",
        }
    def _capture_target(target_name: str) -> Dict[str, Any]:
        vm_result = aegis_vm_client.vm_screenshot(target=str(target_name or "aegis_tab"), timeout_s=20.0, overlay_cursor=True)
        result: Dict[str, Any] = {"vm": vm_result, "target": str(target_name or "aegis_tab")}
        if not bool(vm_result.get("ok")):
            return result
        screenshot_path_local = resolve_artifact_path(str(vm_result.get("screenshot_path") or ""))
        if screenshot_path_local is None or not screenshot_path_local.exists():
            result["error"] = "screenshot_path_unavailable"
            return result
        try:
            from common.ocr_dual_tool import ocr_dual
            ocr_result = dict(
                ocr_dual(
                    {
                        "path": str(screenshot_path_local),
                        "goal": (message or "Read and summarize the student loan repayment page").strip(),
                        "engine": "rapidocr",
                        "max_chars": 4000,
                        "route_mode": "balanced",
                    }
                )
                or {}
            )
        except Exception as exc:
            ocr_result = {"ok": False, "error": f"ocr_exception:{type(exc).__name__}:{exc}"}
        text_local = _redact_text(str(ocr_result.get("text") or ocr_result.get("markdown") or ocr_result.get("raw_text") or "").strip())
        result.update(
            {
                "screenshot_path": screenshot_path_local,
                "ocr": ocr_result,
                "text": text_local,
                "sample_lines": _sample_lines(text_local),
            }
        )
        return result

    primary_target = str(session.get("target") or "aegis_tab")
    attempt = _capture_target(primary_target)
    if not bool((attempt.get("vm") or {}).get("ok")):
        return {
            "ok": False,
            "error": str((attempt.get("vm") or {}).get("error") or "vm_capture_failed"),
            "session": _public_session(session),
            "capture": {"vm_status": attempt.get("vm") or {}},
            "reply": f"Apollo could not capture the VM screen: {(attempt.get('vm') or {}).get('error') or 'vm_capture_failed'}.",
        }
    if (not attempt.get("sample_lines")) and primary_target == "aegis_tab":
        fallback_attempt = _capture_target("direct_console")
        if fallback_attempt.get("sample_lines"):
            attempt = fallback_attempt
    screenshot_path = attempt.get("screenshot_path")
    if screenshot_path is None or not Path(str(screenshot_path)).exists():
        return {
            "ok": False,
            "error": "screenshot_path_unavailable",
            "session": _public_session(session),
            "capture": {"vm_status": attempt.get("vm") or {}},
            "reply": "Apollo received a VM capture response, but the screenshot artifact was not readable from the shared workspace.",
        }
    ocr = dict(attempt.get("ocr") or {})
    redacted = str(attempt.get("text") or "")
    previous_capture = session.get("last_capture") if isinstance(session.get("last_capture"), dict) else {}
    comparison = _compare_text(str(previous_capture.get("text") or ""), redacted) if compare_last or previous_capture else {"available": False, "summary": "No prior capture available for comparison."}
    capture = {
        "captured_at": _now_iso(),
        "message": str(message or "").strip()[:400],
        "screenshot_path": str(screenshot_path),
        "capture_target": str(attempt.get("target") or primary_target),
        "capture_source": str((attempt.get("vm") or {}).get("capture_source") or (attempt.get("vm") or {}).get("execution_surface") or ""),
        "ocr_engine": str(ocr.get("engine") or "gb10_auto"),
        "page_kind": _page_kind(redacted),
        "sample_lines": list(attempt.get("sample_lines") or []),
        "takeaways": _takeaways(redacted),
        "comparison": comparison,
        "text": redacted[:12000],
    }
    with _STATE_LOCK:
        session = _read_session_unlocked(session_id)
        prior = session.get("last_capture") if isinstance(session.get("last_capture"), dict) else {}
        if prior:
            session["previous_capture"] = prior
        session["last_capture"] = capture
        _write_session_unlocked(session)
    public_session = get_session(session_id)
    return {
        "ok": True,
        "session": public_session,
        "capture": _public_capture(capture),
        "reply": _build_reply(capture, public_session),
    }


def handle_chat_message(session_id: str, message: str) -> Dict[str, Any] | None:
    text = str(message or "").strip()
    if not text:
        return None
    low = text.lower()
    session = get_session(session_id)
    active = bool(session.get("mode_active"))
    mentions_loans = any(term in low for term in _LOAN_TERMS)
    is_rag_policy_question = any(term in low for term in ("rag", "ingest", "ocr97", "mode=ocr", "duplicate", "dedupe", "metadata", "guardrail"))
    wants_capture = any(phrase in low for phrase in ("look at this", "screen grab", "screenshot", "capture this", "what do you see", "read this", "summarize this page", "what page is this", "analyze this page", "what am i looking at", "this page"))
    wants_compare = any(phrase in low for phrase in ("compare this", "compare to the last", "compare with the last", "what changed", "how is this different"))
    wants_status = "loan review" in low and any(token in low for token in ("status", "on", "off", "mode"))
    wants_resume = any(token in low for token in ("resume capture", "resume loan review", "resume screen capture", "capture can resume", "done logging in"))
    wants_pause = any(token in low for token in ("pause capture", "pause loan review", "blind mode", "hide login", "pause for login"))
    wants_enable = (
        any(token in low for token in ("student loan review mode", "loan review mode", "start loan review", "enable loan review"))
        and not wants_status
        and "?" not in low
    )

    if wants_resume and (active or mentions_loans):
        return {
            **set_mode(session_id, mode_active=True, capture_paused=False, pause_reason=""),
            "reply": "Student-loan review mode is active and capture is resumed. Ask Apollo to look at the current page when you want a screen grab.",
        }
    if wants_pause and (active or mentions_loans):
        return {
            **set_mode(session_id, mode_active=True, capture_paused=True, pause_reason="login_blind_mode"),
            "reply": "Student-loan review mode is active, but capture is paused for login blind mode.",
        }
    if wants_enable:
        result = set_mode(session_id, mode_active=True, capture_paused=True, pause_reason="login_blind_mode")
        result["reply"] = "Student-loan review mode is active. Capture is paused until you finish login or MFA and tell Apollo to resume."
        return result
    if wants_status and (active or mentions_loans):
        paused = bool(session.get("capture_paused"))
        state_text = "paused" if paused else "ready"
        reason = str(session.get("pause_reason") or "")
        reply = f"Student-loan review mode is on. Capture state: {state_text}."
        if reason:
            reply += f" Reason: {reason}."
        last_capture = session.get("last_capture") if isinstance(session.get("last_capture"), dict) else {}
        if last_capture:
            reply += f" Last capture: {last_capture.get('captured_at') or 'unknown'}."
        return {"ok": True, "session": session, "reply": reply}
    if wants_compare and active and not is_rag_policy_question:
        return capture_screen(session_id, message=text, compare_last=True)
    if wants_capture and (active or mentions_loans) and not is_rag_policy_question:
        return capture_screen(session_id, message=text, compare_last=False)
    return None
