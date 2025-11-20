"""
routes.py - Console API Routes for Apollo

Provides endpoints for the Apollo Console web interface.
"""

import os
import time
import requests
from flask import Blueprint, jsonify, request, render_template, current_app
from pathlib import Path

from apollo_console.logging import (
    start_conversation,
    append_turn,
    load_conversation,
    list_conversations,
    get_conversation_exists
)
from apollo_rag.retriever import get_retriever
from config import Config

# Get model names from config
GENERATION_MODEL = Config.GENERATION_MODEL
EMBEDDING_MODEL = Config.EMBEDDING_MODEL

bp_console = Blueprint("bp_console", __name__)


def _call_ollama_generate(prompt: str, model: str = None) -> str:
    """
    Call Ollama generate API.

    Args:
        prompt: The prompt to send
        model: Model name (defaults to GENERATION_MODEL)

    Returns:
        Generated text response
    """
    model = model or GENERATION_MODEL
    ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")

    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": prompt,
                "stream": False
            },
            timeout=120
        )
        response.raise_for_status()
        return response.json().get("response", "")
    except requests.exceptions.RequestException as e:
        return f"Error calling model: {e}"


def _build_rag_prompt(query: str, context_docs: list) -> str:
    """
    Build a RAG-augmented prompt.

    Args:
        query: User query
        context_docs: Retrieved documents

    Returns:
        Formatted prompt
    """
    context_text = ""
    if context_docs:
        context_parts = []
        for doc in context_docs[:5]:
            text = doc.get("text", "")[:500]
            kind = doc.get("meta", {}).get("kind", "unknown")
            context_parts.append(f"[{kind}] {text}")
        context_text = "\n\n".join(context_parts)

    if context_text:
        prompt = f"""You are Apollo, a financial assistant. Use the following context to answer the question.

Context:
{context_text}

Question: {query}

Answer clearly and concisely based on the context provided. If the context doesn't contain relevant information, say so."""
    else:
        prompt = f"""You are Apollo, a financial assistant. Answer the following question clearly and concisely.

Question: {query}

Answer:"""

    return prompt


@bp_console.route("/console")
def console_page():
    """Serve the console HTML page."""
    return render_template("console.html")


@bp_console.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Chat endpoint for the console.

    Request JSON:
        {
            "message": "string",
            "conversation_id": "optional string"
        }

    Response JSON:
        {
            "conversation_id": "string",
            "reply": "string",
            "context": [...],
            "metadata": {...}
        }
    """
    start_time = time.time()

    data = request.get_json() or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "message required"}), 400

    # Get or create conversation
    conversation_id = data.get("conversation_id")
    if not conversation_id or not get_conversation_exists(conversation_id):
        conversation_id = start_conversation()

    # Log user message
    append_turn(conversation_id, "user", message)

    # Retrieve context
    retriever = get_retriever()
    rag_result = retriever.search(message, top_k=5, include_scores=True)
    results = rag_result.get("results", [])

    # Build context response
    context = []
    retrieved_kinds = []
    retrieved_ids = []

    for doc in results:
        doc_id = doc.get("id", "")
        kind = doc.get("meta", {}).get("kind", "unknown")
        source = doc.get("meta", {}).get("source", "")
        score = doc.get("distance", 0)

        context.append({
            "id": doc_id,
            "kind": kind,
            "source": source,
            "score": round(score, 4)
        })
        retrieved_kinds.append(kind)
        retrieved_ids.append(doc_id)

    # Build prompt and generate response
    prompt = _build_rag_prompt(message, results)
    reply = _call_ollama_generate(prompt)

    # Calculate latency
    latency_ms = int((time.time() - start_time) * 1000)

    # Log assistant response
    append_turn(
        conversation_id,
        "assistant",
        reply,
        retrieved_kinds=retrieved_kinds,
        retrieved_ids=retrieved_ids,
        notes="RAG-augmented" if results else "no context",
        metadata={
            "model": GENERATION_MODEL,
            "latency_ms": latency_ms,
            "context_count": len(results)
        }
    )

    return jsonify({
        "conversation_id": conversation_id,
        "reply": reply,
        "context": context,
        "metadata": {
            "model": GENERATION_MODEL,
            "retriever": EMBEDDING_MODEL,
            "latency_ms": latency_ms
        }
    })


@bp_console.route("/api/conversations", methods=["GET"])
def api_conversations():
    """
    List recent conversations.

    Returns:
        List of conversation summaries
    """
    limit = int(request.args.get("limit", 20))
    conversations = list_conversations(limit=limit)
    return jsonify({"conversations": conversations})


@bp_console.route("/api/conversations/<conversation_id>", methods=["GET"])
def api_conversation_detail(conversation_id):
    """
    Get full conversation transcript.

    Args:
        conversation_id: The conversation ID

    Returns:
        List of turns
    """
    turns = load_conversation(conversation_id)
    if not turns:
        return jsonify({"error": "Conversation not found"}), 404

    return jsonify({
        "conversation_id": conversation_id,
        "turns": turns
    })


@bp_console.route("/api/plots/recent", methods=["GET"])
def api_plots_recent():
    """
    Get recent plots/artifacts.

    Returns:
        List of plot metadata
    """
    plots_dir = Path(__file__).parent.parent / "static" / "plots"

    if not plots_dir.exists():
        return jsonify({"plots": []})

    # Find PNG files
    plots = []
    for png_file in sorted(plots_dir.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)[:10]:
        plots.append({
            "filename": png_file.name,
            "url": f"/static/plots/{png_file.name}",
            "modified": png_file.stat().st_mtime
        })

    return jsonify({"plots": plots})


@bp_console.route("/api/health", methods=["GET"])
def api_health():
    """Health check endpoint."""
    try:
        retriever = get_retriever()
        stats = retriever.get_collection_stats()

        return jsonify({
            "status": "healthy",
            "collection": stats.get("collection_name"),
            "document_count": stats.get("document_count", 0),
            "generation_model": GENERATION_MODEL,
            "embedding_model": EMBEDDING_MODEL
        })
    except Exception as e:
        return jsonify({
            "status": "unhealthy",
            "error": str(e)
        }), 500
