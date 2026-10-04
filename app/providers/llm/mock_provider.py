"""
MockLLMProvider (Master Blueprint §92: "All external providers must
have mock implementations for tests").

Returns deterministic, canned responses with no network call —
supports a simple scripted-response queue so a test can control
exactly what the "model" says next, including scripting a tool call.

Usage in a test:

    from app.providers.llm.mock_provider import MockLLMProvider
    from app.providers.llm.interface import LLMResponse

    mock = MockLLMProvider(responses=[
        LLMResponse(content="Hello!", model_used="mock", prompt_tokens=0,
                    completion_tokens=0, provider="mock"),
    ])
    service = CompanionService(db, llm=mock)   # providers are injected, not monkeypatched
"""
from __future__ import annotations

from typing import Optional

from app.providers.llm.interface import ChatMessage, LLMProvider, LLMResponse


class MockLLMProvider(LLMProvider):
    def __init__(self, responses: Optional[list[LLMResponse]] = None):
        self._responses = list(responses or [])
        self.calls: list[dict] = []  # test-inspectable call log

    def queue_response(self, response: LLMResponse) -> None:
        self._responses.append(response)

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
        self.calls.append(
            {"messages": messages, "temperature": temperature, "tools": tools, "tier": tier, "max_tokens": max_tokens}
        )

        if self._responses:
            return self._responses.pop(0)

        return LLMResponse(
            content="This is a mock response.",
            model_used="mock-model",
            prompt_tokens=0,
            completion_tokens=0,
            provider="mock",
        )
