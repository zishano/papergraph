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
        self.model = "gemini-2.0-flash-exp"  # Fast model for translation

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

        try:
            response = await self.http.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"Content-Type": "application/json", "X-goog-api-key": self.api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048},
                },
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            translated = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            return translated
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Translation failed: {type(exc).__name__}")
