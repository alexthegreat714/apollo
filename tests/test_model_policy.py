from __future__ import annotations

import pytest

from Apollo import model_policy


def test_deep_trade_model_prefers_qwen72b(monkeypatch):
    monkeypatch.setattr(
        model_policy,
        "ollama_installed_models",
        lambda base_url: ["gemma3:12b", "qwen2.5:72b"],
    )

    policy = model_policy.resolve_model_policy(probe=True, environ={})

    assert policy["fast_model"]["model"] == "gemma3:12b"
    assert policy["deep_trade_model"]["base_url"] == "http://127.0.0.1:11434"
    assert policy["deep_trade_model"]["model"] == "qwen2.5:72b"
    assert policy["deep_trade_model"]["fallback_used"] is False


def test_deep_trade_model_falls_back_to_next_installed(monkeypatch):
    monkeypatch.setattr(
        model_policy,
        "ollama_installed_models",
        lambda base_url: ["gemma-3-27b-it-Q4_K_M:latest", "gemma3:12b"],
    )

    policy = model_policy.resolve_model_policy(probe=True, environ={})

    assert policy["deep_trade_model"]["model"] == "gemma-3-27b-it-Q4_K_M:latest"
    assert policy["deep_trade_model"]["fallback_used"] is True


def test_model_storage_blocks_c_drive_downloads():
    env = {
        "APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS": "0",
        "APOLLO_MODEL_STORAGE_ROOT": r"D:\ApolloModels",
        "HF_HOME": r"C:\Users\bob\.cache\huggingface",
    }

    result = model_policy.validate_model_storage(environ=env)

    assert result["ok"] is False
    assert result["blocked_c_drive_paths"]["HF_HOME"].startswith("C:")


def test_model_storage_allows_d_drive_defaults():
    env = {
        "APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS": "0",
        "APOLLO_MODEL_STORAGE_ROOT": r"D:\ApolloModels",
    }

    result = model_policy.validate_model_storage(environ=env)

    assert result["ok"] is True
    assert result["resolved_paths"]["OLLAMA_MODELS"].startswith("D:")


def test_assert_model_storage_raises_on_c_drive():
    env = {
        "APOLLO_ALLOW_C_DRIVE_MODEL_DOWNLOADS": "0",
        "APOLLO_MODEL_STORAGE_ROOT": r"C:\ApolloModels",
    }

    with pytest.raises(RuntimeError, match="model_storage_c_drive_blocked"):
        model_policy.assert_model_storage_allowed(environ=env)
