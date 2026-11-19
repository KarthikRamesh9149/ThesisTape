"""Responses API transport and Standard short-context rates for the research models."""

import json
import os
import re
import shlex
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .disney_hulu_case import CaseError

MODEL = "gpt-6.2-sol"
LUNA = "gpt-6-luna"
TERRA = "gpt-6.1-terra"
ASTRA = "gpt-6.2-astra"
OPENAI_URL = "https://api.openai.com/v1"
MAX_OUTPUT_TOKENS = 4096
RATES = {
    "ordinary_input": "2.00",
    "cached_input": "0.10",
    "cache_write_30m": "2.50",
    "output": "10.00",
}
PRICING_URL = "https://developers.openai.com/api/docs/models/gpt-6.2-sol"
MODEL_RATES = {
    MODEL: RATES,
    LUNA: {"ordinary_input": "0.10", "cached_input": "0.01", "cache_write_30m": "0.125", "output": "0.50"},
    TERRA: {"ordinary_input": "1.00", "cached_input": "0.05", "cache_write_30m": "1.25", "output": "5.00"},
    ASTRA: {"ordinary_input": "8.00", "cached_input": "0.40", "cache_write_30m": "10.00", "output": "40.00"},
}
MILLION = Decimal(1000000)


def project_key(path: Path) -> str:
    values = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip().removeprefix("export ").strip() == "OPENAI_API_KEY":
                try:
                    parts = shlex.split(value, comments=True)
                except ValueError:
                    raise CaseError("Invalid OPENAI_API_KEY assignment in the project env file") from None
                if len(parts) != 1:
                    raise CaseError("Invalid OPENAI_API_KEY assignment in the project env file")
                values.append(parts[0])
    if len(values) > 1:
        raise CaseError("The project env file defines OPENAI_API_KEY more than once")
    key = values[0] if values else os.environ.get("OPENAI_API_KEY", "")
    if not re.fullmatch(r"sk-[A-Za-z0-9_-]+", key):
        raise CaseError("Set OPENAI_API_KEY in the project .env.local or shell environment")
    return key


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class Result:
    body: dict
    request_id: str | None
    elapsed_ms: int


class OpenAIResponses:
    def __init__(self, key: str):
        self._key = key

    def _post(self, path: str, payload: dict) -> Result:
        request = Request(
            f"{OPENAI_URL}/{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
            method="POST",
        )
        start = perf_counter()
        try:
            with build_opener(NoRedirect()).open(request, timeout=120) as response:
                request_id = response.headers.get("x-request-id")
                body = json.load(response)
        except HTTPError as error:
            # API error messages can echo request values. Keep diagnostics to coded fields.
            try:
                fields = json.loads(error.read(8192)).get("error", {})
                details = [fields.get("code"), fields.get("param")]
                details = [v for v in details if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.]+", v)]
            except (ValueError, AttributeError):
                details = []
            suffix = f" ({', '.join(details)})" if details else ""
            raise CaseError(f"OpenAI {path} returned HTTP {error.code}{suffix}") from None
        except (URLError, TimeoutError, OSError):
            raise CaseError(f"OpenAI {path} did not return a confirmed response") from None
        except ValueError:
            raise CaseError(f"OpenAI {path} returned an invalid JSON response") from None
        if not isinstance(body, dict):
            raise CaseError(f"OpenAI {path} returned an unexpected response")
        return Result(body, request_id, round((perf_counter() - start) * 1000))

    def count(self, prompt: dict) -> int:
        return nonnegative_integer(self._post("responses/input_tokens", prompt).body.get("input_tokens"))

    def generate(self, payload: dict) -> Result:
        return self._post("responses", payload)


def nonnegative_integer(value) -> int:
    if type(value) is not int or value < 0:
        raise CaseError("Invalid or missing provider token count")
    return value


def reservation_cost(input_tokens: int, max_output_tokens: int, model: str) -> Decimal:
    rates = MODEL_RATES[model]
    tokens = nonnegative_integer(input_tokens)
    if not 1 <= nonnegative_integer(max_output_tokens) <= MAX_OUTPUT_TOKENS:
        raise CaseError("Invalid model output limit")
    if tokens > 272000:
        raise CaseError("This baseline uses the short-context Standard rates")
    # Reserve at the cache-write rate even when a cache write is not expected.
    return (tokens * Decimal(rates["cache_write_30m"]) + max_output_tokens * Decimal(rates["output"])) / MILLION


class BudgetExhausted(CaseError):
    """A dispatch would exceed the remaining assignment allowance."""


def reserve(input_tokens: int, budget: Decimal, *, max_output_tokens: int = MAX_OUTPUT_TOKENS, model: str = MODEL) -> Decimal:
    upper = reservation_cost(input_tokens, max_output_tokens, model)
    if not budget.is_finite() or budget <= 0:
        raise CaseError("The USD budget must be a positive finite amount")
    if upper > budget:
        raise BudgetExhausted(f"Required reservation USD {upper:f} exceeds budget USD {budget:f}")
    return upper


def usage_cost(body: dict, *, expected_model: str = MODEL) -> Decimal:
    if body.get("service_tier") != "default":
        raise CaseError("The provider did not confirm Standard service")
    model = body.get("model", "")
    rates = MODEL_RATES[expected_model]
    if not isinstance(model, str) or not re.fullmatch(re.escape(expected_model) + r"(?:-\d{4}-\d{2}-\d{2})?", model):
        raise CaseError(f"The provider did not return the requested model {expected_model}")
    usage = body.get("usage")
    if not isinstance(usage, dict):
        raise CaseError("Missing provider usage")
    input_tokens = nonnegative_integer(usage.get("input_tokens"))
    output_tokens = nonnegative_integer(usage.get("output_tokens"))
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        raise CaseError("Missing provider input-token breakdown")
    cached = nonnegative_integer(details.get("cached_tokens", 0))
    writes = nonnegative_integer(details.get("cache_write_tokens", 0))
    cache_detail = details.get("cache_write_tokens_details")
    if cache_detail is not None and (
        not isinstance(cache_detail, dict)
        or nonnegative_integer(cache_detail.get("ttl_12h_tokens", 0)) != 0
        or nonnegative_integer(cache_detail.get("ttl_30m_tokens", 0)) != writes
    ):
        raise CaseError("The provider cache-write breakdown differs from the requested 30-minute policy")
    if "total_tokens" in usage and nonnegative_integer(usage["total_tokens"]) != input_tokens + output_tokens:
        raise CaseError("Provider token totals do not reconcile")
    if cached + writes > input_tokens or input_tokens > 272000:
        raise CaseError("Provider usage cannot be priced under the baseline's Standard rates")
    return (
        (input_tokens - cached - writes) * Decimal(rates["ordinary_input"])
        + cached * Decimal(rates["cached_input"])
        + writes * Decimal(rates["cache_write_30m"])
        + output_tokens * Decimal(rates["output"])
    ) / MILLION


def structured_text(body: dict) -> str:
    if body.get("status") != "completed":
        raise CaseError("The provider response did not complete")
    texts = []
    for item in body.get("output", []):
        if item.get("type") == "reasoning":
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise CaseError("Unexpected provider output item")
        for content in item["content"]:
            if content.get("type") != "output_text" or not isinstance(content.get("text"), str):
                raise CaseError("The provider did not return a structured memo")
            texts.append(content["text"])
    if len(texts) != 1:
        raise CaseError("Expected exactly one structured memo")
    return texts[0]
