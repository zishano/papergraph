import json
from pathlib import Path

import httpx

from papergraph.gemini import GeminiScorer
from papergraph.models import CitationGraph


def graph():
    return CitationGraph.model_validate_json((Path(__file__).resolve().parents[1] /
        "validation/llmshare-dse/graph-citing.json").read_text())


async def test_missing_key_records_skip_without_network(monkeypatch):
    monkeypatch.delenv("GOOGLE_AI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    def handler(request):
        raise AssertionError("Missing API key must not make a request")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        records = await GeminiScorer(http).analyze(graph(), "DSE")
    assert records
    assert {r["status"] for r in records} == {"skipped_missing_api_key"}


async def test_free_model_rotation_parses_summary_and_score():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if "gemini-3-flash-preview" in request.url.path:
            return httpx.Response(429)
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps({
            "score": 82, "summary": "提出面向芯粒的高效架构设计方法", "reason": "方法完整且实验充分",
            "translation": "中文摘要"
        }, ensure_ascii=False)}]}}]}
        return httpx.Response(200, json=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        scorer = GeminiScorer(http, api_key="test", interval=0)
        record = await scorer._score("W1", "Title", "Abstract", "DSE")
    assert record["status"] == "completed"
    assert record["score"] == 82
    assert record["model"] == "gemini-3.1-flash-lite"
    assert record["translation"] == "中文摘要"
    assert len(calls) == 2


async def test_transient_503_retries_same_model():
    calls = 0
    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
            "score": 75, "summary": "总结", "reason": "理由", "translation": "翻译"
        }, ensure_ascii=False)}]}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        scorer = GeminiScorer(http, api_key="test", model="gemini-3-flash-preview", interval=0)
        record = await scorer._score("W1", "Title", "Abstract", "DSE")
    assert calls == 2
    assert record["status"] == "completed"
