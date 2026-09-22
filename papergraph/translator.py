"""Google AI translation for abstracts."""
import asyncio
import json
import os
import time

import httpx


class GoogleAITranslator:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None, interval: float = 1.0):
        self.http = http
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.interval = interval
        self.last_request = 0.0
        configured_model = os.getenv("PAPERGRAPH_GEMINI_MODEL", "auto")
        self.models = ([configured_model] if configured_model != "auto" else [
            "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
            "gemini-3.5-flash-lite", "gemini-flash-lite-latest",
        ])

    async def translate_batch(self, texts: list[str]) -> list[str]:
        """Translate a batch of English texts to Chinese."""
        if not self.api_key:
            return ["未提供翻译API密钥"] * len(texts)

        results = []
        for text in texts:
            if not text or not text.strip():
                results.append("")
                continue

            # Rate limiting
            wait = self.interval - (time.monotonic() - self.last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self.last_request = time.monotonic()

            try:
                translated = await self._translate_one(text)
                results.append(translated)
            except Exception as e:
                results.append(f"翻译失败: {str(e)}")

        return results

    async def _translate_one(self, text: str) -> str:
        """Translate a single text."""
        prompt = f"""请将以下英文学术摘要翻译成中文，保持专业术语准确，语句通顺自然。
只返回翻译结果，不要任何额外说明或格式。

原文：
{text}

中文翻译："""

        errors = []
        for model in self.models:
            try:
                response = await self.http.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"Content-Type": "application/json", "X-goog-api-key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
                    }, timeout=60,
                )
                if response.status_code in {400, 404, 429, 502, 503}:
                    errors.append(f"{model}:HTTP {response.status_code}")
                    continue
                response.raise_for_status()
                data = response.json()
                translated = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if translated:
                    return translated
                errors.append(f"{model}:empty response")
            except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                errors.append(f"{model}:{type(exc).__name__}")
        raise ValueError("Translation failed: " + "; ".join(errors))
