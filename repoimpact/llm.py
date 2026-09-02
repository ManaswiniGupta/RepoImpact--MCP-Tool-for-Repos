"""LLM provider abstraction for explain()'s natural-language synthesis step.

This module is only responsible for turning already-computed structured
facts (produced by impact.py, graph.py, workflow.py) into prose. It never
retrieves anything and is never given repository source — only the compact
evidence dict `build_explanation()` (mcp_server.py) already assembled.

Keeping this behind `LLMProvider` means the rest of RepoImpact has no
provider-specific code; swapping Gemini for another provider later is a new
subclass, not a rewrite.
"""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

_SYSTEM_INSTRUCTION = (
    "You are RepoImpact's explain assistant. You are given a question about "
    "a Python repository and a compact JSON bundle of structured evidence "
    "gathered by deterministic static analysis — a resolved symbol, its "
    "call-graph-derived change impact, and its execution workflow — never "
    "the repository's source code itself. Answer using only this evidence. "
    "Cite specific symbols and files by name. If the evidence doesn't "
    "support a claim, say so rather than guessing."
)


class LLMProvider(ABC):
    @abstractmethod
    def explain(self, question: str, context: dict[str, Any]) -> str:
        """Turn a compact structured-context dict into a natural-language answer."""
        raise NotImplementedError


def _build_prompt(question: str, context: dict[str, Any]) -> str:
    return f"Question: {question}\n\nEvidence (JSON):\n{json.dumps(context, indent=2, default=str)}"


class GeminiProvider(LLMProvider):
    """Google Gemini, via the `google-genai` SDK.

    Pass `client` directly (any object exposing `.models.generate_content(...)`)
    to bypass API-key resolution entirely — this is how tests inject a fake
    client instead of making a real API call.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_GEMINI_MODEL,
        client: Any = None,
    ):
        self._model = model
        if client is not None:
            self._client = client
            return

        from google import genai  # imported lazily: optional until an LLM is actually used

        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set (and no api_key was passed)"
            )
        self._client = genai.Client(api_key=key)

    def explain(self, question: str, context: dict[str, Any]) -> str:
        # A plain dict is accepted anywhere GenerateContentConfig is (the SDK
        # types it as GenerateContentConfigDict) — using it here means an
        # injected fake client never needs `google.genai` importable at all.
        response = self._client.models.generate_content(
            model=self._model,
            contents=_build_prompt(question, context),
            config={"system_instruction": _SYSTEM_INSTRUCTION},
        )
        return response.text


def load_default_provider() -> LLMProvider | None:
    """Construct the configured provider from environment variables, or
    None if none is configured. Every `explain()` caller must work fine with
    None — the LLM only adds prose on top of facts that already exist."""
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiProvider()
    return None
