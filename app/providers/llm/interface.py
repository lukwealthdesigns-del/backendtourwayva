"""LLM provider abstraction (Master Blueprint sections 35, 39-40, 97)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


# Chat messages are passed through to the provider close to verbatim
# (system/user/assistant/tool roles, plus OpenAI-style `tool_calls` /
# `tool_call_id` fields on the relevant messages), so a plain dict is
# used rather than a narrow TypedDict - the shape genuinely varies by
# role in the tool-calling protocol.
ChatMessage = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    provider: str
    used_fallback: bool = False
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tool_call(self) -> bool:
        return len(self.tool_calls) > 0


class LLMProvider(ABC):
    @abstractmethod
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
        """`tier` selects the model class (Blueprint §97): "fast" (cheap:
        classification, summaries, memory extraction), "strong" (planning),
        or "default". `timeout` overrides the per-request timeout in seconds.

        Generate a chat completion, optionally offering `tools`
        (OpenAI function-calling schema - see
        app/modules/companion/tools.py for Tour-Wayva's tool
        definitions). If the model chooses to call one or more tools,
        `LLMResponse.tool_calls` is populated and `content` may be
        empty - the caller is responsible for executing the tools and
        making a follow-up call with the results appended as
        `role: "tool"` messages (see CompanionService).

        Must implement cost-aware primary/fallback model routing
        internally (Blueprint section 97) and raise
        ProviderUnavailableError - never fabricate a response - if
        both models fail (section 79).
        """
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        on_delta: Callable[[Optional[str]], Awaitable[None]],
        temperature: float = 0.7,
        max_tokens: int = 800,
        tools: Optional[list[dict]] = None,
        tier: str = "default",
        timeout: Optional[float] = None,
    ) -> LLMResponse:
        """Like `generate`, but forwards the answer's text to `on_delta(text)` as it is produced. `on_delta(None)` means
        "discard what you showed so far": the model started with some text and then decided to call a tool, so that text
        was only a preamble. Returns the complete LLMResponse exactly as `generate` would. The default implementation
        does not stream: it generates normally and forwards the finished text once, so providers that cannot stream
        (and test doubles) keep working."""
        response = await self.generate(
            messages, temperature=temperature, max_tokens=max_tokens, tools=tools, tier=tier, timeout=timeout
        )
        if response.content and not response.tool_calls:
            await on_delta(response.content)
        return response
