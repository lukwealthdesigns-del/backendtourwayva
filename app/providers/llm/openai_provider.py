"""
OpenAI chat completion provider.

Implements Blueprint section 97 (cost-aware AI routing) at the
simplest useful level: try AI_PRIMARY_MODEL first; if that call fails
(rate limit, timeout, 5xx), retry once against AI_FALLBACK_MODEL. If
both fail, raise ProviderUnavailableError - per section 79, a failed
generation must never produce a corrupted/fabricated response.

Also implements tool-calling (function calling) pass-through for the
Companion tool system (sections 39-40): when `tools` is supplied, it's
forwarded to OpenAI as-is, and any `tool_calls` in the response are
parsed into `LLMResponse.tool_calls`. Tool *execution* and
authorization happen entirely outside this provider, in
app/modules/companion/tools.py - this class only speaks the OpenAI
wire protocol.

Model routing by task class: callers pass tier="fast" (classification,
summaries, memory extraction) or tier="strong" (itinerary planning);
AI_FAST_MODEL / AI_STRONG_MODEL override the primary model for that tier.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from app.core.config import settings
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.core.redaction import redact_secrets
from app.core.resilience import CircuitOpenError
from app.providers.http_resilience import resilient_request
from app.providers.llm.interface import ChatMessage, LLMProvider, LLMResponse, ToolCall

logger = get_logger(__name__)

_CHAT_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider(LLMProvider):
    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 800,
        tools: Optional[list[dict]] = None,
        tier: str = "default",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        if not settings.OPENAI_API_KEY:
            raise ProviderUnavailableError("AI provider is not configured.")

        primary = self.model_for_tier(tier)
        request_timeout = timeout or settings.AI_REQUEST_TIMEOUT_SECONDS
        try:
            return await self._call_model(
                primary, messages, temperature, max_tokens, tools, used_fallback=False, timeout=request_timeout
            )
        except ProviderUnavailableError:
            logger.warning("openai_primary_failed_trying_fallback", model=primary)

        try:
            return await self._call_model(
                settings.AI_FALLBACK_MODEL, messages, temperature, max_tokens, tools,
                used_fallback=True, timeout=request_timeout,
            )
        except ProviderUnavailableError as exc:
            logger.error("openai_fallback_also_failed", model=settings.AI_FALLBACK_MODEL)
            raise ProviderUnavailableError("The AI assistant is temporarily unavailable. Please try again.") from exc

    @staticmethod
    def model_for_tier(tier: str) -> str:
        """Cost-aware routing (Blueprint §97): do not spend the strongest model
        on a one-word intent classification."""
        chosen = {"fast": settings.AI_FAST_MODEL, "strong": settings.AI_STRONG_MODEL}.get(tier)
        return chosen or settings.AI_PRIMARY_MODEL

    async def _call_model(
        self,
        model: str,
        messages: list[ChatMessage],
        temperature: float,
        max_tokens: int,
        tools: Optional[list[dict]],
        *,
        used_fallback: bool,
        timeout: float = 30.0,
    ) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }
        body: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        async def attempt() -> dict:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(_CHAT_URL, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()

        # NOT idempotent by default: a tool-calling turn with side-effecting tools is not safe to
        # silently retry as a NEW model call (the model could choose different tool arguments the
        # second time). Still protected by timeout + circuit breaker; one attempt only.
        try:
            data = await resilient_request(f"openai_{model}", attempt, idempotent=tools is None)
        except CircuitOpenError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error("openai_call_failed", model=model, error=redact_secrets(exc))
            raise ProviderUnavailableError("AI generation failed.") from exc

        message = data["choices"][0]["message"]
        usage = data.get("usage", {})

        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(ToolCall(id=raw_call["id"], name=function.get("name", ""), arguments=arguments))

        return LLMResponse(
            content=message.get("content") or "",
            model_used=data.get("model", model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            provider="openai",
            used_fallback=used_fallback,
            tool_calls=tool_calls,
        )
