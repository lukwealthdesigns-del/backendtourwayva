import asyncio

from app.providers.email.mock_provider import MockEmailProvider
from app.providers.llm.interface import LLMResponse
from app.providers.llm.mock_provider import MockLLMProvider


def test_mock_email_provider_records_otp_email():
    provider = MockEmailProvider()
    asyncio.run(
        provider.send_otp_email(
            to_email="test@example.com", first_name="Ada", otp_code="123456", purpose_label="email verification"
        )
    )
    sent = provider.find_sent_to("test@example.com")
    assert len(sent) == 1
    assert "123456" in sent[0].html_content


def test_mock_email_provider_no_network_call():
    """The whole point: this runs with zero network access and still
    'sends' successfully — proving the mock never touches Brevo."""
    provider = MockEmailProvider()
    result = asyncio.run(
        provider.send_transactional_email(to_email="a@b.com", subject="Hi", html_content="<p>Hi</p>")
    )
    assert result is True
    assert len(provider.sent) == 1


def test_mock_llm_provider_returns_queued_response_in_order():
    first = LLMResponse(content="first", model_used="mock", prompt_tokens=0, completion_tokens=0, provider="mock")
    second = LLMResponse(content="second", model_used="mock", prompt_tokens=0, completion_tokens=0, provider="mock")
    provider = MockLLMProvider(responses=[first, second])

    r1 = asyncio.run(provider.generate([{"role": "user", "content": "hi"}]))
    r2 = asyncio.run(provider.generate([{"role": "user", "content": "hi again"}]))

    assert r1.content == "first"
    assert r2.content == "second"


def test_mock_llm_provider_default_response_when_queue_empty():
    provider = MockLLMProvider()
    result = asyncio.run(provider.generate([{"role": "user", "content": "anything"}]))
    assert result.content == "This is a mock response."


def test_mock_llm_provider_logs_calls():
    provider = MockLLMProvider()
    asyncio.run(provider.generate([{"role": "user", "content": "track me"}], temperature=0.2))
    assert len(provider.calls) == 1
    assert provider.calls[0]["temperature"] == 0.2
