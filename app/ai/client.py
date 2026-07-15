from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from jinja2 import Environment, FileSystemLoader

from app.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


class AIClient:
    """OpenRouter LLM client with Jinja2 prompt rendering."""

    def __init__(self) -> None:
        self._api_key = settings.openrouter_api_key
        self._model = settings.openrouter_model
        self._base_url = "https://openrouter.ai/api/v1/chat/completions"
        self._jinja_env = Environment(
            loader=FileSystemLoader(str(PROMPTS_DIR)),
            autoescape=False,
        )
        self._http = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=5,
                keepalive_expiry=60,
            ),
        )

    async def analyze(
        self,
        template_path: str,
        context: dict[str, Any],
        parse_json: bool = True,
    ) -> dict[str, Any] | str:
        """Render a Jinja2 prompt template, call OpenRouter, parse response.

        Args:
            template_path: Relative path under prompts/ (e.g. "github/project_evaluation.jinja2")
            context: Variables to inject into the template
            parse_json: If True, attempt to parse the response as JSON
        """
        template = self._jinja_env.get_template(template_path)
        prompt = template.render(**context)

        response_text = await self._call_llm(prompt)

        if parse_json:
            return self._extract_json(response_text)
        return response_text

    async def _call_llm(self, prompt: str, max_retries: int = 3) -> str:
        """Call OpenRouter chat completions API with retry on transient errors."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }

        for attempt in range(max_retries):
            try:
                resp = await self._http.post(self._base_url, headers=headers, json=payload)
                if resp.status_code == 429 or resp.status_code >= 500:
                    wait = min(2 ** attempt * 3, 30)
                    logger.warning(
                        "OpenRouter API %d (model=%s), retry %d/%d in %ds",
                        resp.status_code, self._model, attempt + 1, max_retries, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code != 200:
                    logger.error("OpenRouter API error %d (model=%s): %s", resp.status_code, self._model, resp.text[:500])
                    return "{}"
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                if not content:
                    logger.warning("OpenRouter returned empty content (model=%s)", self._model)
                    return "{}"
                return content
            except asyncio.CancelledError:
                logger.warning("OpenRouter API call cancelled (shutdown?)")
                raise
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
                wait = min(2 ** attempt * 3, 30)
                logger.warning(
                    "OpenRouter API transient error (model=%s, attempt %d/%d): %s, retry in %ds",
                    self._model, attempt + 1, max_retries, type(exc).__name__, wait,
                )
                # Rebuild HTTP client to discard stale connections
                try:
                    await self._http.aclose()
                except Exception:
                    pass
                self._http = httpx.AsyncClient(
                    timeout=120.0,
                    limits=httpx.Limits(
                        max_connections=20,
                        max_keepalive_connections=5,
                        keepalive_expiry=60,
                    ),
                )
                await asyncio.sleep(wait)
            except Exception:
                logger.exception("OpenRouter API call failed (model=%s)", self._model)
                return "{}"

        logger.error("OpenRouter API failed after %d retries (model=%s)", max_retries, self._model)
        return "{}"

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Extract JSON from LLM response, handling markdown code blocks."""
        text = text.strip()
        # Strip markdown code block
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines (``` markers)
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse LLM response as JSON, returning raw")
            return {"raw": text}

    async def close(self) -> None:
        await self._http.aclose()
