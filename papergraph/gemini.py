"""Optional Google AI Studio paper summaries and scores."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time

import httpx

from papergraph.models import CitationGraph


DEFAULT_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]


class GeminiScorer:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None,
                 model: str = "auto", interval: float = 4.1):
        self.http = http
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.models = ([model] if model != "auto" else DEFAULT_MODELS.copy())
        self.interval = interval
        self.last_request = 0.0

    async def analyze(self, graph: CitationGraph, keywords: str) -> list[dict]:
        papers = [n for n in sorted(graph.nodes, key=lambda n: (n.hop, n.id))
                  if n.id not in graph.seed_ids]
        if not self.api_key:
            return [{"paper_id": p.id, "status": "skipped_missing_api_key",
                     "model": None, "summary": "", "score": None, "reason": ""}
                    for p in papers]
        records = []
        for paper in papers:
            if not paper.abstract:
                records.append({"paper_id": paper.id, "status": "skipped_missing_abstract",
                                "model": None, "summary": "", "score": None,
                                "reason": "缺少摘要，未调用 Gemini"})
                continue
            records.append(await self._score(paper.id, paper.title, paper.abstract, keywords))
        return records

    async def _score(self, paper_id: str, title: str, abstract: str, keywords: str) -> dict:
        prompt = self._prompt(title, abstract, keywords)
        errors = []
        for model in self.models:
            wait = self.interval - (time.monotonic() - self.last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_request = time.monotonic()
            try:
                response = await self.http.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"Content-Type": "application/json", "X-goog-api-key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024,
                                             "responseMimeType": "application/json"},
                    }, timeout=60,
                )
                if response.status_code in {400, 404, 429, 502, 503}:
                    errors.append(f"{model}:HTTP {response.status_code}")
                    continue
                response.raise_for_status()
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = self._parse(text)
                return {"paper_id": paper_id, "status": "completed", "model": model,
                        "summary": parsed["summary"], "score": parsed["score"],
                        "reason": parsed["reason"]}
            except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"{model}:{type(exc).__name__}")
        return {"paper_id": paper_id, "status": "failed", "model": None,
                "summary": "", "score": None, "reason": "; ".join(errors)}

    @staticmethod
    def _parse(value: str) -> dict:
        value = value.strip()
        match = re.search(r"\{.*\}", value, re.S)
        if not match:
            raise ValueError("Gemini response did not contain JSON")
        data = json.loads(match.group())
        score = float(data["score"])
        if not 0 <= score <= 100:
            raise ValueError("Gemini score must be between 0 and 100")
        summary = str(data.get("summary", "")).strip()
        reason = str(data.get("reason", "")).strip()
        if not summary:
            raise ValueError("Gemini summary is empty")
        return {"score": score, "summary": summary, "reason": reason}

    @staticmethod
    def _prompt(title: str, abstract: str, keywords: str) -> str:
        return f"""请评估并总结下面的学术论文。只返回一个 JSON 对象，不要 Markdown 或额外文字。

标题：{title}
摘要：{abstract}
用户关注关键词：{keywords or '未指定'}

评分由四项相加得到 0–100 分：创新性、实用性、严谨性、清晰度，每项 0–25 分。
严格评分，多数普通论文应在 60–80 分，通常不超过 85 分。

必须返回：
{{"score": 72, "summary": "中文一句话概括核心贡献，不超过60字", "reason": "中文简述评分理由，不超过80字"}}"""
