"""OpenAI client for generating bilingual copy."""

from __future__ import annotations

from typing import Dict, Optional

import httpx
from loguru import logger

from app.config import settings
from app.schemas import GeneratedCopy, YapPayload

PROMPT_TEMPLATE = """Role: Crypto "yapping" copywriter.
Requirements:
- Output three blocks: 1) Korean sentence (150-200 chars), 2) English sentence (120-180 chars), 3) Three hashtags (#tag format).
- Ban exaggerated profit language, FUD, or defamatory phrasing. Keep numbers conservative.
- Do not embed URLs inside sentences.
Input: {title} | source={source} | tags={tags}
Output:
[KO]
[EN]
[TAG]
"""


class OpenAICopyClient:
    """Wrapper around the OpenAI chat completions endpoint."""

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = api_key or settings.openai_api_key
        self.model = model or settings.openai_model
        self.endpoint = "https://api.openai.com/v1/chat/completions"

    async def generate_copy(self, payload: YapPayload) -> GeneratedCopy:
        """Request bilingual copy from OpenAI."""

        if not self.api_key:
            raise RuntimeError("OpenAI API key is missing.")

        prompt = PROMPT_TEMPLATE.format(
            title=payload.title,
            source=payload.source or "unknown",
            tags=", ".join(payload.tags) if payload.tags else "none",
        )

        body: Dict[str, object] = {
            "model": self.model,
            "temperature": 0.7,
            "messages": [
                {"role": "system", "content": "You write concise, energetic crypto yapping posts."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 320,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
            )

        if response.status_code >= 400:
            logger.error("OpenAI request failed: {} - {}", response.status_code, response.text)
            raise RuntimeError(f"OpenAI request failed with status {response.status_code}")

        payload_json = response.json()
        content = payload_json["choices"][0]["message"]["content"]
        parsed = self._parse_response(content)
        usage = payload_json.get("usage", {})

        return GeneratedCopy(
            text_ko=parsed.get("ko", ""),
            text_en=parsed.get("en", ""),
            hashtags=parsed.get("tag", ""),
            prompt_version=settings.prompt_version,
            token_usage=usage.get("total_tokens"),
        )

    @staticmethod
    def _parse_response(content: str) -> Dict[str, str]:
        """Convert formatted response into parts."""

        parsed: Dict[str, str] = {"ko": "", "en": "", "tag": ""}
        for line in content.splitlines():
            striped = line.strip()
            if striped.startswith("[KO]"):
                parsed["ko"] = striped.replace("[KO]", "").strip()
            elif striped.startswith("[EN]"):
                parsed["en"] = striped.replace("[EN]", "").strip()
            elif striped.startswith("[TAG]"):
                parsed["tag"] = striped.replace("[TAG]", "").strip()
        return parsed
