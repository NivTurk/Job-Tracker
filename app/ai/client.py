"""The one place the app talks to the Claude API.

Model name, timeout, retries and prices are set here. Everything else calls
ClaudeClient.complete() and gets back an AiResponse or an AiApiError, so the
rest of the code never imports the SDK and tests can swap in a fake.
"""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import anthropic
import httpx2

# Changing the model changes the cache key, so every application shows
# "AI pending" again until it is re-scored with the new model.
MODEL = "claude-sonnet-5-5"

# Per request. A score is usually 5-20 seconds.
TIMEOUT_SECONDS = 60.0

# The SDK retries connection errors, 408/409/429 and 5xx with backoff.
# This is separate from the single retry on invalid output (app/services/ai_scores.py).
MAX_RETRIES = 2

# A full answer is usually 600-1200 tokens. A reply cut off at the limit is
# retried once with the higher value.
MAX_TOKENS = 2000
RETRY_MAX_TOKENS = 4000

# USD per million tokens: input, output, cache write (5 min), cache read.
# From https://platform.claude.com/docs/en/about-claude/pricing (October 2026).
# Haiku 5.5 costs more above 100k prompt tokens; a CV plus a JD never gets close.
PRICES: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {
    "claude-sonnet-5-5": (Decimal("2"), Decimal("10"), Decimal("2.50"), Decimal("0.10")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50"), Decimal("0.125"), Decimal("0.01")),
    "claude-opus-5-5": (Decimal("4"), Decimal("20"), Decimal("5"), Decimal("0.40")),
}

_MILLION = Decimal(1_000_000)


@dataclass
class AiResponse:
    text: str
    stop_reason: str | None
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    request_id: str | None = None
    latency_ms: int = 0


class AiApiError(Exception):
    """The API could not be reached or refused the request, after the SDK's retries."""

    def __init__(self, outcome: str, message: str, latency_ms: int = 0, request_id: str | None = None) -> None:
        super().__init__(message)
        self.outcome = outcome  # "api_error" or "timeout"
        self.message = message
        self.latency_ms = latency_ms
        self.request_id = request_id


class AiClient(Protocol):
    model: str

    def complete(
        self, system: str, messages: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int
    ) -> AiResponse: ...


def estimate_cost(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
) -> Decimal | None:
    """Estimated USD cost of one call. None for a model missing from PRICES."""
    prices = PRICES.get(model)
    if prices is None or input_tokens is None or output_tokens is None:
        return None
    price_in, price_out, price_write, price_read = prices
    total = (
        input_tokens * price_in
        + output_tokens * price_out
        + (cache_write_tokens or 0) * price_write
        + (cache_read_tokens or 0) * price_read
    ) / _MILLION
    return total.quantize(Decimal("0.000001"))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class ClaudeClient:
    def __init__(
        self,
        api_key: str,
        model: str = MODEL,
        timeout: float = TIMEOUT_SECONDS,
        max_retries: int = MAX_RETRIES,
        http_client: httpx2.Client | None = None,  # tests pass one with a mock transport
    ) -> None:
        self.model = model
        self._client = anthropic.Anthropic(
            api_key=api_key, timeout=timeout, max_retries=max_retries, http_client=http_client
        )

    def complete(
        self, system: str, messages: list[dict[str, Any]], schema: dict[str, Any], max_tokens: int
    ) -> AiResponse:
        started = time.monotonic()
        try:
            message = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,  # type: ignore[arg-type]
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APITimeoutError as exc:
            raise AiApiError("timeout", "The request to the Claude API timed out.", _elapsed_ms(started)) from exc
        except anthropic.APIConnectionError as exc:
            raise AiApiError("api_error", "Could not connect to the Claude API.", _elapsed_ms(started)) from exc
        except anthropic.AuthenticationError as exc:
            raise AiApiError(
                "api_error", "The API key was rejected. Check ANTHROPIC_API_KEY in .env.",
                _elapsed_ms(started), exc.request_id,
            ) from exc
        except anthropic.APIStatusError as exc:
            raise AiApiError(
                "api_error", f"The Claude API returned {exc.status_code}: {_short(exc.message)}",
                _elapsed_ms(started), exc.request_id,
            ) from exc

        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return AiResponse(
            text=text,
            stop_reason=message.stop_reason,
            # The requested ID, not message.model: that one can carry a date
            # suffix and would miss the PRICES table.
            model=self.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_input_tokens,
            cache_write_tokens=usage.cache_creation_input_tokens,
            request_id=getattr(message, "_request_id", None),
            latency_ms=_elapsed_ms(started),
        )


def _short(text: str, limit: int = 300) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
