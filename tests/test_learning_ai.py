import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scorer import learning_ai, server
from scorer.learning_ai import (
    FALLBACK_BETA,
    LearningGenerator,
    MissingApiKeyError,
    RefusedError,
)

AVAILABLE = frozenset("水をくださいみずおねがいしますカフェラテ準備確認すし")


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)
        self.beta = SimpleNamespace(messages=FakeMessages(response))
        self.chat = SimpleNamespace(completions=FakeMessages(response))

    @property
    def calls(self):
        return self.messages.calls + self.beta.messages.calls + self.chat.completions.calls


def reply(payload, *, stop_reason="end_turn", prefix=()):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            *prefix,
            SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False)),
        ],
    )


def chat_reply(payload, *, finish_reason="stop", refusal=None):
    content = json.dumps(payload, ensure_ascii=False)
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(finish_reason=finish_reason, message=message)])


def generator_for(response, **options):
    client = FakeClient(response)
    return LearningGenerator(client_factory=lambda: client, **options), client


def test_daily_keeps_only_sentences_the_app_can_teach():
    generator, client = generator_for(reply({"sentences": [
        {"japanese": "水をください", "reading": "みずをください", "korean": "물을 주세요", "usage": "식당에서"},
        {"japanese": "OKです", "reading": "おーけーです", "korean": "좋아요", "usage": "대답"},
        {"japanese": "珈琲をください", "reading": "こーひーをください", "korean": "커피 주세요", "usage": "카페"},
        {"japanese": "お願いします", "reading": "おねがいします", "korean": "부탁합니다", "usage": "부탁"},
    ]}), model="claude-haiku-5-5")

    entries = generator.generate_daily("카페에서 주문하기", AVAILABLE)

    assert [entry["text"] for entry in entries] == ["水をください"]
    assert entries[0]["source"] == "claude" and entries[0]["kind"] == "daily"
    assert client.beta.messages.calls == []
    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in call and "betas" not in call
    assert call["output_config"]["effort"] == "low"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "<situation>카페에서 주문하기</situation>" in call["messages"][0]["content"]


def test_server_fallback_only_for_models_that_support_it():
    payload = {"sentences": [{"japanese": "水", "reading": "みず", "korean": "물", "usage": ""}]}
    generator, client = generator_for(reply(payload), model="claude-opus-5-5")
    generator.generate_daily("식당", AVAILABLE)
    call = client.beta.messages.calls[0]
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"


def test_baze_key_routes_through_the_gateway_with_bearer_auth(monkeypatch):
    monkeypatch.setattr(learning_ai, "load_local_environment", lambda: None)
    monkeypatch.delenv("LINGO_CLAUDE_BASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "direct-placeholder")
    monkeypatch.setenv("BAZE_API_KEY", "gateway-placeholder")

    assert learning_ai.provider_settings() == {
        "auth_token": "gateway-placeholder",
        "base_url": learning_ai.BAZE_BASE_URL,
    }
    generator = LearningGenerator(model="claude-opus-5-5")
    generator._get_client()
    assert generator.uses_server_fallback() is False

    monkeypatch.delenv("LINGO_OPENAI_BASE_URL", raising=False)
    assert learning_ai.openai_settings() == {
        "api_key": "gateway-placeholder",
        "base_url": learning_ai.BAZE_OPENAI_BASE_URL,
    }

    monkeypatch.delenv("BAZE_API_KEY")
    assert learning_ai.provider_settings() == {"api_key": "direct-placeholder"}
    assert learning_ai.openai_settings() is None


def test_default_gemini_model_uses_gateway_chat_completions(monkeypatch):
    monkeypatch.delenv("LINGO_AI_MODEL", raising=False)
    monkeypatch.delenv("LINGO_CLAUDE_MODEL", raising=False)
    generator, client = generator_for(chat_reply({"sentences": [
        {"japanese": "水をください", "reading": "みずをください", "korean": "물을 주세요", "usage": "식당에서"},
    ]}))

    entries = generator.generate_daily("식당", AVAILABLE)

    assert generator.model == "gemini-3.5-flash-lite" and generator.is_claude is False
    assert [entry["text"] for entry in entries] == ["水をください"]
    assert client.messages.calls == [] and client.beta.messages.calls == []
    call = client.chat.completions.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["messages"][0]["role"] == "system"
    assert call["response_format"]["type"] == "json_schema"
    assert call["response_format"]["json_schema"]["strict"] is True
    assert call["response_format"]["json_schema"]["name"] == "daily_sentences"

    blocked, _ = generator_for(chat_reply({"sentences": []}, finish_reason="content_filter"))
    with pytest.raises(RefusedError):
        blocked.generate_daily("식당", AVAILABLE)


def test_jlpt_needs_kanji_and_skips_words_already_shown():
    generator, client = generator_for(reply({"words": [
        {"word": "準備", "reading": "じゅんび", "korean": "준비", "example_japanese": "準備する", "example_korean": "준비하다"},
        {"word": "すし", "reading": "すし", "korean": "초밥", "example_japanese": "", "example_korean": ""},
        {"word": "確認", "reading": "かくにん", "korean": "확인", "example_japanese": "", "example_korean": ""},
    ]}), model="claude-haiku-5-5")

    entries = generator.generate_jlpt("N3", ["確認"], AVAILABLE)

    assert [entry["text"] for entry in entries] == ["準備"]
    assert entries[0]["group"] == "N3" and entries[0]["estimated"] is True
    assert entries[0]["usage"] == "예문: 準備する — 준비하다"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "JLPT N3" in prompt and "確認" in prompt


def test_refusal_and_fallback_switch_are_handled():
    generator, _ = generator_for(reply({"sentences": []}, stop_reason="refusal"), model="claude-haiku-5-5")
    with pytest.raises(RefusedError):
        generator.generate_daily("상황", AVAILABLE)

    switched = reply(
        {"sentences": [{"japanese": "水", "reading": "みず", "korean": "물", "usage": ""}]},
        prefix=(SimpleNamespace(type="text", text="{partial"), SimpleNamespace(type="fallback")),
    )
    generator, _ = generator_for(switched, model="claude-haiku-5-5")
    assert generator.generate_daily("상황", AVAILABLE)[0]["text"] == "水"


def test_missing_key_is_reported_without_calling_claude(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BAZE_API_KEY", raising=False)
    monkeypatch.setattr(learning_ai, "load_local_environment", lambda: None)
    generator = LearningGenerator()

    assert generator.configured() is False
    with pytest.raises(MissingApiKeyError):
        generator.generate_daily("상황", AVAILABLE)


def test_generate_route_returns_entries_and_enforces_limits(monkeypatch):
    generator, _ = generator_for(reply({"sentences": [
        {"japanese": "水をください", "reading": "みずをください", "korean": "물을 주세요", "usage": "식당에서"},
    ]}), model="claude-haiku-5-5")
    monkeypatch.setattr(server, "_learning_generator", generator)
    monkeypatch.setattr(server, "_learning_request_budget", server.TeacherRequestBudget(1, 10))
    client = TestClient(server.app)

    missing = client.post("/learn/generate", json={"kind": "daily", "situation": "  "})
    assert missing.status_code == 422
    assert missing.json()["detail"]["code"] == "SITUATION_REQUIRED"

    ok = client.post("/learn/generate", json={"kind": "daily", "situation": "식당"})
    assert ok.status_code == 200
    assert ok.json()["entries"][0]["text"] == "水をください"

    limited = client.post("/learn/generate", json={"kind": "daily", "situation": "식당"})
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "AI_RATE_LIMITED"


def test_generate_route_explains_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("BAZE_API_KEY", raising=False)
    monkeypatch.setattr(learning_ai, "load_local_environment", lambda: None)
    monkeypatch.setattr(server, "_learning_generator", LearningGenerator())
    client = TestClient(server.app)

    response = client.post("/learn/generate", json={"kind": "jlpt", "level": "N5"})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AI_NOT_CONFIGURED"
