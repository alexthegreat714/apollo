import logging
import os
import time
import json

from flask import Blueprint, Response, current_app, jsonify, request, send_file

from common.rag_store import AgentRAG, read_jsonl_bomtolerant
from .runtime_metrics import record_event

bp_rag = Blueprint("bp_rag", __name__)
RAG = AgentRAG(agent_name="Aegis")

# --- Task 03: scoring readout endpoint ---
@bp_rag.route("/rag/settings", methods=["GET"])
def rag_settings():
    from common.rag_store import RAG_W_SIM, RAG_W_PRI, RAG_W_AGE, TOPK_DEFAULT

    return jsonify(
        {
            "ok": True,
            "weights": {"sim": RAG_W_SIM, "priority": RAG_W_PRI, "age": RAG_W_AGE},
            "topk_default": TOPK_DEFAULT,
        }
    )


# --- end Task 03 ---

# --- Import whitelist guard (Task 01) ---
IMPORT_ROOT = r"C:\Users\blyth\Desktop\Engineering"
IMPORT_LOG = r"C:\Users\blyth\Desktop\Engineering\Aegis\logs\import_attempts.jsonl"


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(path or "")))


def _is_within(root: str, candidate: str) -> bool:
    root_n = _norm(root)
    cand_n = _norm(candidate)
    return cand_n.startswith(root_n + os.sep)


def _log_import_attempt(path: str, ok: bool, reason: str) -> None:
    try:
        os.makedirs(os.path.dirname(IMPORT_LOG), exist_ok=True)
        rec = {"ts": time.time(), "path": path, "ok": ok, "reason": reason}
        with open(IMPORT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must not break the route


def _validate_import_path(path: str):
    if not path or not isinstance(path, str):
        _log_import_attempt(str(path), False, "missing_or_nonstring")
        return (False, 400, "Missing or invalid 'path'")

    cand = _norm(path)

    # 1) Whitelist boundary first → 403
    if not _is_within(IMPORT_ROOT, cand):
        _log_import_attempt(cand, False, "outside_whitelist")
        return (False, 403, f"Path not allowed (root whitelist: {IMPORT_ROOT})")

    # 2) Extension check → 415
    if not cand.lower().endswith(".jsonl"):
        _log_import_attempt(cand, False, "bad_extension")
        return (False, 415, "Only .jsonl files are accepted")

    # 3) Existence → 404
    if not os.path.exists(cand):
        _log_import_attempt(cand, False, "not_found")
        return (False, 404, "File not found")

    _log_import_attempt(cand, True, "ok")
    return (True, 200, "ok")
# --- end Import whitelist guard ---


@bp_rag.route("/rag/write", methods=["POST"])
def rag_write():
    js = request.get_json() or {}
    text = (js.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    priority = float(js.get("priority", 0.5))
    if priority < 0.8:
        meta = {k: v for k, v in js.items() if k != "text"}
        meta.setdefault("source", "api")
        meta.setdefault("kind", "note")
        meta["priority"] = priority
        entry_id = meta.get("id") or f"shortterm_{int(time.time() * 1000)}"
        meta["id"] = entry_id
        RAG.write_short_term(text, meta)
        record_event("write")
        return jsonify({"ok": True, "id": entry_id, "short_term": True})
    doc_id = RAG.remember(
        text=text,
        source=js.get("source", "api"),
        kind=js.get("kind", "note"),
        priority=float(js.get("priority", 0.5)),
        tags=js.get("tags") or [],
        extra=js.get("extra") or {},
        id_=js.get("id"),
    )
    record_event("write")
    return jsonify({"ok": True, "id": doc_id})


@bp_rag.route("/rag/shortterm/list", methods=["GET"])
def rag_shortterm_list():
    limit = int(request.args.get("limit", 100))
    since_raw = request.args.get("since_ts")
    since_ts = float(since_raw) if since_raw else None
    items = RAG.read_short_term(limit=limit, since_ts=since_ts)
    return jsonify({"items": items})


@bp_rag.route("/rag/shortterm/export", methods=["GET"])
def rag_shortterm_export():
    path = RAG._short_term_path()
    if not os.path.exists(path):
        return Response("", mimetype="application/json")

    def generate():
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                yield line

    headers = {"Content-Disposition": 'attachment; filename="short_term.jsonl"'}
    return Response(generate(), mimetype="application/json", headers=headers)


@bp_rag.route("/rag/appendix", methods=["POST"])
def rag_appendix():
    body = request.get_json(silent=True) or {}
    max_items = int(body.get("max_items", 50))
    summarize = bool(body.get("summarize", True))
    clear_after = bool(body.get("clear_after", True))
    result = RAG.appendix_promote(max_items=max_items, summarize=summarize)
    if clear_after and result.get("promoted", 0) > 0:
        result["short_term_cleared"] = RAG.clear_short_term()
    result["ok"] = True
    record_event("appendix")
    return jsonify(result)


@bp_rag.route("/rag/search", methods=["POST"])
def rag_search():
    js = request.get_json() or {}
    q = (js.get("query") or "").strip()
    if not q:
        return jsonify({"ok": False, "error": "query required"}), 400
    res = RAG.search(
        query=q,
        top_k=int(js.get("top_k", 6)),
        min_priority=float(js.get("min_priority", 0.0)),
        since_ts=js.get("since_ts"),
        kinds=js.get("kinds"),
        tags_any=js.get("tags_any"),
    )
    record_event("search")
    return jsonify({"ok": True, "data": res})


@bp_rag.route("/rag/review", methods=["POST"])
def rag_review():
    try:
        data = request.get_json(silent=True) or {}
        min_priority = 0.8
        top_k = int(data.get("top_k", 64))
        where_filter = data.get("where")

        candidates = RAG.search(
            query=data.get("query", "") or "",
            top_k=top_k,
            min_priority=min_priority,
            since_ts=None,
        ).get("results", [])

        if where_filter:
            def _match(meta: dict) -> bool:
                for key, value in where_filter.items():
                    if (meta or {}).get(key) != value:
                        return False
                return True

            candidates = [hit for hit in candidates if _match(hit.get("meta") or {})]

        existing = set(RAG.topic_signatures({"kind": "summary", "source": "review"}))
        created = []
        for hit in candidates:
            text = (hit.get("text") or "").strip()
            if not text:
                continue
            sig = RAG.signature_for_text(text)
            if sig in existing:
                continue
            summary = RAG.summarize_block(text)
            new_id = RAG.remember(
                text=summary,
                source="review",
                kind="summary",
                priority=0.9,
                extra={"topic_signature": sig},
            )
            existing.add(sig)
            created.append({"id": new_id, "topic_signature": sig})
        record_event("review")
        return jsonify({"created": len(created), "items": created})
    except Exception as exc:
        logging.exception("Review error")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/count", methods=["GET"])
def rag_count():
    try:
        n = RAG.count()
        return jsonify({"ok": True, "count": n})
    except Exception as exc:
        current_app.logger.exception("rag_count failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/count", methods=["POST"])
def rag_count_post():
    try:
        body = request.get_json(silent=True) or {}
        where = body.get("where")
        if where == {}:
            where = None
        elif where is not None and not isinstance(where, dict):
            return jsonify({"ok": False, "error": "where must be an object"}), 400
        min_priority = body.get("min_priority")
        min_p = None
        if min_priority is not None:
            try:
                min_p = float(min_priority)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "min_priority must be numeric"}), 400
        n = RAG.count(where=where, min_priority=min_p)
        return jsonify({"ok": True, "count": n})
    except Exception as exc:
        current_app.logger.exception("rag_count_post failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/export", methods=["GET"])
def rag_export():
    try:
        out_path = r"C:\Users\blyth\Desktop\Engineering\Aegis\logs\rag_export.jsonl"
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        RAG.export_jsonl(out_path)
        return send_file(out_path, as_attachment=True, download_name="rag_export.jsonl")
    except Exception as exc:
        current_app.logger.exception("rag_export failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/import", methods=["POST"])
def rag_import():
    try:
        body = request.get_json(silent=True) or {}
        path = body.get("path")

        # Task 01: Import whitelist guard
        ok, code, msg = _validate_import_path(path)
        if not ok:
            return jsonify({"ok": False, "error": msg}), code
        imported = skipped = 0
        for item in read_jsonl_bomtolerant(path):
            if not item:
                skipped += 1
                continue
            text = item.get("text")
            meta = item.get("meta")
            if not isinstance(text, str) or not text.strip() or not isinstance(meta, dict):
                skipped += 1
                continue
            text_val = text.strip()
            source = meta.get("source")
            source_val = source.strip() if isinstance(source, str) and source.strip() else "import"
            kind = meta.get("kind")
            kind_val = kind.strip() if isinstance(kind, str) and kind.strip() else "note"
            priority_raw = meta.get("priority")
            try:
                priority_val = float(priority_raw) if priority_raw is not None else 0.5
            except (TypeError, ValueError):
                priority_val = 0.5
            tags_raw = meta.get("tags")
            tags: list[str] = []
            if isinstance(tags_raw, str):
                tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
            elif isinstance(tags_raw, (list, tuple)):
                tags = [str(t).strip() for t in tags_raw if str(t).strip()]
            extra = {}
            for key, value in meta.items():
                if key in {"source", "kind", "priority", "tags"}:
                    continue
                if isinstance(value, (str, int, float, bool)):
                    extra[key] = value
            doc_id = item.get("id")
            if doc_id is not None:
                doc_id = str(doc_id)
            try:
                RAG.remember(
                    text=text_val,
                    source=source_val,
                    kind=kind_val,
                    priority=priority_val,
                    tags=tags,
                    extra=extra,
                    id_=doc_id,
                )
                imported += 1
            except Exception:
                current_app.logger.exception("rag_import item failed")
                skipped += 1
        return jsonify({"ok": True, "imported": imported, "skipped": skipped})
    except Exception as exc:
        current_app.logger.exception("rag_import failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/list", methods=["GET"])
def rag_list():
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        ids = RAG.list_ids(limit=limit, offset=offset)
        return jsonify({"ids": ids, "limit": limit, "offset": offset})
    except Exception as exc:
        current_app.logger.exception("rag_list failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/list", methods=["POST"])
def rag_list_post():
    try:
        body = request.get_json(silent=True) or {}
        where = body.get("where")
        if where == {}:
            where = None
        elif where is not None and not isinstance(where, dict):
            return jsonify({"ok": False, "error": "where must be an object"}), 400
        limit = max(1, min(int(body.get("limit", 50)), 200))
        offset = max(0, int(body.get("offset", 0)))
        result = RAG.get(
            ids=body.get("ids"),
            where=where,
            limit=limit,
            offset=offset,
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        current_app.logger.exception("rag_list_post failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/get", methods=["POST"])
def rag_get():
    try:
        body = request.get_json(force=True) or {}
        where = body.get("where")
        if where == {}:
            where = None
        result = RAG.get(
            ids=body.get("ids"),
            where=where,
            limit=int(body.get("limit", 100)),
            offset=int(body.get("offset", 0)),
        )
        return jsonify(result)
    except Exception as exc:
        current_app.logger.exception("rag_get failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/delete", methods=["POST"])
def rag_delete():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    where = body.get("where")
    if where == {}:
        where = None
    if not ids and not where:
        return jsonify({"ok": False, "error": "Provide ids or a where filter"}), 400
    try:
        deleted = RAG.delete(ids=ids, where=where)
        return jsonify({"deleted": deleted, "ok": True})
    except Exception as exc:
        current_app.logger.exception("rag_delete failed")
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/update", methods=["POST"])
def rag_update():
    # TODO(Task 04): Deprecated for metadata-only changes. Prefer /rag/update_meta.
    body = request.get_json(silent=True) or {}
    ids = body.get("ids")
    where = body.get("where")
    updates = {k: v for k, v in body.items() if k in ("priority", "tags", "source", "kind")}
    if not updates:
        return jsonify({"ok": False, "error": "No updatable fields provided"}), 400
    try:
        data = RAG.col.get(ids=ids) if ids else RAG.col.get(where=where)
        got_ids = data.get("ids", [])
        docs = data.get("documents", [])
        metas = data.get("metadatas", [])
        if not got_ids:
            return jsonify({"ok": True, "updated": 0})
        new_metas = []
        for m in metas:
            m = (m or {}).copy()
            for k, v in updates.items():
                m[k] = v
            if isinstance(m.get("tags"), list):
                m["tags"] = ",".join(str(t) for t in m["tags"])
            new_metas.append(m)
        RAG.col.delete(ids=got_ids)
        RAG.col.add(ids=got_ids, documents=docs, metadatas=new_metas)
        return jsonify({"ok": True, "updated": len(got_ids)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@bp_rag.route("/rag/update_meta", methods=["POST"])
def rag_update_meta():
    """
    Metadata-only update that avoids re-embedding.
    Body:
      {
        "where": {"id": "<uuid>"}  # or any Chroma where filter on metadata
        "set": {"priority":0.92, "tags":["x"], "source":"...", "kind":"...", "agent":"...", "extra":{...}}
      }
    """
    js = request.get_json(silent=True) or {}
    where = js.get("where")
    to_set = js.get("set") or {}
    if not isinstance(to_set, dict) or not to_set:
        return jsonify({"ok": False, "error": "set must be a non-empty object"}), 400

    allowed = {"priority", "tags", "source", "kind", "agent", "extra"}
    bad = [k for k in to_set.keys() if k not in allowed]
    if bad:
        return jsonify({"ok": False, "error": f"fields not allowed in meta update: {bad}"}), 400

    ids = []
    if where and isinstance(where, dict) and "id" in where:
        ids = [where["id"]]
        data = RAG.col.get(ids=ids, include=["documents", "metadatas"])
    else:
        data = RAG.col.get(where=where or {}, include=["documents", "metadatas"])
        ids = data.get("ids", []) or []

    if not ids:
        return jsonify({"ok": True, "updated": 0, "ids": [], "path": "none"})

    metas = data.get("metadatas", []) or []
    docs = data.get("documents", []) or []

    if not metas or len(metas) != len(ids):
        return jsonify({"ok": True, "updated": 0, "ids": [], "path": "none"})

    new_metas = []
    for m in metas:
        m = dict(m or {})
        for k, v in to_set.items():
            m[k] = v
        new_metas.append(m)

    try:
        RAG.col.update(ids=ids, metadatas=new_metas)
        return jsonify({"ok": True, "updated": len(ids), "ids": ids, "path": "update"})
    except Exception as e:
        if not docs or len(docs) != len(ids):
            return jsonify({"ok": False, "error": f"update not supported and no docs available for fallback: {e}"}), 501
        try:
            RAG.col.delete(ids=ids)
            RAG.col.add(ids=ids, documents=docs, metadatas=new_metas)
            return jsonify({"ok": True, "updated": len(ids), "ids": ids, "path": "delete+add"})
        except Exception as e2:
            return jsonify({"ok": False, "error": f"fallback failed: {e2}"}), 500


@bp_rag.route("/rag/tags", methods=["GET"])
def rag_tags():
    data = RAG.col.get(include=["metadatas"])
    tags = set()
    for m in data.get("metadatas", []):
        if not m:
            continue
        val = m.get("tags")
        if isinstance(val, str):
            tags.update(t.strip() for t in val.split(",") if t.strip())
    return jsonify({"ok": True, "tags": sorted(tags)})
