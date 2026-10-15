"""One tiny API call to check the key, the model name and structured outputs.

    python -m app.ai.check

Costs a fraction of a cent. Nothing is written to the database.
"""

import json
import sys

from app.ai.client import AiApiError, ClaudeClient, estimate_cost
from app.config import get_config

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "language": {"type": "string"}},
    "required": ["ok", "language"],
    "additionalProperties": False,
}


def main() -> int:
    key = get_config().anthropic_api_key.strip()
    if not key:
        print("ANTHROPIC_API_KEY is empty. Add it to .env first.")
        return 1
    client = ClaudeClient(key)
    print(f"Calling {client.model}...")
    try:
        response = client.complete(
            "Answer in JSON.",
            [{"role": "user", "content": 'Set ok to true and language to the language of this word: "שלום"'}],
            SCHEMA,
            100,
        )
    except AiApiError as exc:
        print(f"Failed ({exc.outcome}): {exc.message}")
        return 1
    try:
        data = json.loads(response.text)
    except json.JSONDecodeError:
        print(f"Got a reply, but it is not JSON: {response.text!r}")
        return 1
    cost = estimate_cost(response.model, response.input_tokens, response.output_tokens)
    print(f"Reply: {data}")
    print(f"Tokens: {response.input_tokens} in, {response.output_tokens} out, "
          f"est. ${cost if cost is not None else '?'}, {response.latency_ms} ms, request {response.request_id}")
    print("OK. AI scoring will work." if data.get("ok") is True else "Unexpected reply, check the model name.")
    return 0 if data.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
