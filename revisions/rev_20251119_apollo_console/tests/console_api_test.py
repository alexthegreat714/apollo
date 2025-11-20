"""
console_api_test.py - Apollo Console API Tests

Tests the console endpoints and conversation logging.

Usage:
    cd C:\\Users\\blyth\\Desktop\\Engineering\\Apollo
    python apollo_tests\\console_api_test.py
"""

import sys
import os
import json
import shutil
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apollo_console.logging import (
    start_conversation,
    append_turn,
    load_conversation,
    list_conversations,
    CONVERSATIONS_DIR
)


def test_conversation_logging():
    """Test basic conversation logging functionality."""
    print("=" * 60)
    print("Test 1: Conversation Logging")
    print("=" * 60)

    # Start conversation
    conv_id = start_conversation()
    assert conv_id and conv_id.startswith("conv_"), "Failed to create conversation"
    print(f"PASS: Created conversation {conv_id}")

    # Verify file exists
    conv_path = Path(CONVERSATIONS_DIR) / f"{conv_id}.jsonl"
    assert conv_path.exists(), "Conversation file not created"
    print(f"PASS: Conversation file exists")

    # Add turns
    append_turn(conv_id, "user", "Test message 1")
    append_turn(
        conv_id,
        "assistant",
        "Test response 1",
        retrieved_kinds=["education"],
        retrieved_ids=["doc1"],
        metadata={"model": "test"}
    )
    append_turn(conv_id, "user", "Test message 2")
    append_turn(conv_id, "assistant", "Test response 2")

    # Load and verify
    turns = load_conversation(conv_id)
    assert len(turns) == 4, f"Expected 4 turns, got {len(turns)}"
    print(f"PASS: Logged 4 turns")

    # Check turn content
    assert turns[0]["role"] == "user", "First turn should be user"
    assert turns[1]["role"] == "assistant", "Second turn should be assistant"
    assert turns[1].get("retrieved_kinds") == ["education"], "Missing retrieved_kinds"
    print("PASS: Turn content correct")

    # List conversations
    convs = list_conversations(limit=10)
    found = any(c["conversation_id"] == conv_id for c in convs)
    assert found, "Conversation not found in list"
    print("PASS: Conversation appears in list")

    print("\nTest 1 PASSED\n")
    return True


def test_api_chat_endpoint():
    """Test the /api/chat endpoint using Flask test client."""
    print("=" * 60)
    print("Test 2: API Chat Endpoint")
    print("=" * 60)

    try:
        # Import the app
        from app import app
        client = app.test_client()

        # Test 1: Send message without conversation_id
        print("\nSending first message...")
        response = client.post(
            '/api/chat',
            json={"message": "What is inflation?"},
            content_type='application/json'
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.get_json()

        assert "conversation_id" in data, "Missing conversation_id"
        assert "reply" in data, "Missing reply"
        assert "context" in data, "Missing context"
        assert "metadata" in data, "Missing metadata"

        conv_id = data["conversation_id"]
        print(f"PASS: First message successful, conv_id={conv_id}")
        print(f"  Reply: {data['reply'][:100]}...")
        print(f"  Context items: {len(data['context'])}")
        print(f"  Latency: {data['metadata'].get('latency_ms', '?')}ms")

        # Verify conversation file was created
        conv_path = Path(CONVERSATIONS_DIR) / f"{conv_id}.jsonl"
        assert conv_path.exists(), "Conversation file not created"
        print("PASS: Conversation file created")

        # Test 2: Send second message with same conversation_id
        print("\nSending second message...")
        response = client.post(
            '/api/chat',
            json={
                "message": "How does it affect investments?",
                "conversation_id": conv_id
            },
            content_type='application/json'
        )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.get_json()
        assert data["conversation_id"] == conv_id, "Conversation ID changed"
        print("PASS: Second message successful")

        # Verify turns logged
        turns = load_conversation(conv_id)
        assert len(turns) >= 4, f"Expected at least 4 turns, got {len(turns)}"
        print(f"PASS: {len(turns)} turns logged")

        # Verify turn structure
        user_turns = [t for t in turns if t["role"] == "user"]
        assistant_turns = [t for t in turns if t["role"] == "assistant"]
        assert len(user_turns) >= 2, "Expected at least 2 user turns"
        assert len(assistant_turns) >= 2, "Expected at least 2 assistant turns"
        print("PASS: Turn structure correct")

        print("\nTest 2 PASSED\n")
        return True

    except ImportError as e:
        print(f"SKIP: Could not import app: {e}")
        print("Make sure all dependencies are installed.")
        return False
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_conversations_endpoint():
    """Test the /api/conversations endpoint."""
    print("=" * 60)
    print("Test 3: API Conversations Endpoint")
    print("=" * 60)

    try:
        from app import app
        client = app.test_client()

        # Get conversations list
        response = client.get('/api/conversations')
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.get_json()
        assert "conversations" in data, "Missing conversations key"

        print(f"PASS: Retrieved {len(data['conversations'])} conversations")

        # If there are conversations, test detail endpoint
        if data["conversations"]:
            conv_id = data["conversations"][0]["conversation_id"]
            response = client.get(f'/api/conversations/{conv_id}')
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"

            detail = response.get_json()
            assert "turns" in detail, "Missing turns key"
            print(f"PASS: Retrieved conversation detail with {len(detail['turns'])} turns")

        print("\nTest 3 PASSED\n")
        return True

    except ImportError as e:
        print(f"SKIP: Could not import app: {e}")
        return False
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_api_health_endpoint():
    """Test the /api/health endpoint."""
    print("=" * 60)
    print("Test 4: API Health Endpoint")
    print("=" * 60)

    try:
        from app import app
        client = app.test_client()

        response = client.get('/api/health')
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"

        data = response.get_json()
        assert "status" in data, "Missing status"
        assert data["status"] == "healthy", f"Status not healthy: {data.get('error')}"

        print(f"PASS: Health check passed")
        print(f"  Collection: {data.get('collection')}")
        print(f"  Document count: {data.get('document_count')}")
        print(f"  Generation model: {data.get('generation_model')}")
        print(f"  Embedding model: {data.get('embedding_model')}")

        print("\nTest 4 PASSED\n")
        return True

    except ImportError as e:
        print(f"SKIP: Could not import app: {e}")
        return False
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    print("\n" + "=" * 60)
    print("APOLLO CONSOLE API TESTS")
    print("=" * 60 + "\n")

    results = []

    # Test 1: Logging module
    results.append(("Conversation Logging", test_conversation_logging()))

    # Test 2: Chat endpoint
    results.append(("API Chat Endpoint", test_api_chat_endpoint()))

    # Test 3: Conversations endpoint
    results.append(("API Conversations Endpoint", test_api_conversations_endpoint()))

    # Test 4: Health endpoint
    results.append(("API Health Endpoint", test_api_health_endpoint()))

    # Summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  {status}: {name}")

    print(f"\nTotal: {passed}/{total} passed")

    if passed == total:
        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
        sys.exit(0)
    else:
        print("\n" + "=" * 60)
        print("SOME TESTS FAILED")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
