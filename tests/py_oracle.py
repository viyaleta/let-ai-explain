"""What the local server's provider code produces, for tests/providers.test.mjs to compare against.

    python3 tests/py_oracle.py   < cases.json   > results.json

cases: [{"kind": "parse", "provider", "file"} | {"kind": "error", "provider", "status", "body", "images"}
        | {"kind": "models", "provider", "fixture"} | {"kind": "build", "provider", "model", "instructions",
        "text", "images", "max_tokens", "effort"} | {"kind": "stream_error", "code", "message"}
        | {"kind": "request_shape", "provider", "key", "model", "instructions", "input"}]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import let_ai_explain as m  # noqa: E402

PARSERS = {"openai": m.parse_openai, "openrouter": m.parse_openrouter, "anthropic": m.parse_anthropic, "gemini": m.parse_gemini}


def run(c):
    k = c["kind"]
    if k == "parse":
        lines = (ROOT / c["file"]).read_bytes().splitlines(keepends=True)
        return list(PARSERS[c["provider"]](m.sse_events(lines)))
    if k == "error":
        e = m.provider_error(c["provider"], c["status"], c["body"].encode(), c.get("images", False))
        return {"code": e.code, "message": e.message, "status": e.status, "retryable": e.retryable}
    if k == "stream_error":
        return m.stream_error(c["code"], c.get("message", ""))
    if k == "models":
        routes = json.loads((ROOT / c["fixture"]).read_text())

        def fake(provider, url, headers, payload=None):
            for part, body in routes.items():
                if url.split("?")[0].endswith(part):
                    return body
            raise m.AppError("model_not_found", "no route", 404)
        real = m.http_json
        m.http_json = fake
        try:
            models, default, extra = m.list_models(c["provider"], "test-key")
        finally:
            m.http_json = real
        return {"models": models, "default": default, **extra}
    if k == "request_shape":  # the model-list call and the answer call, as the server makes them
        seen = []

        def record(provider, url, headers, payload=None):
            seen.append({"url": url, "headers": headers})
            return {"models": [{"name": "models/gemini-3-flash", "supportedGenerationMethods": ["generateContent"]}]}
        real = m.http_json
        m.http_json = record
        try:
            m.list_models(c["provider"], c["key"])
        finally:
            m.http_json = real
        url, body = m.build_request(c["provider"], c["model"], c["instructions"], c["input"], [], 8192, None)
        return {"models_url": seen[0]["url"], "key_headers": m.key_headers(c["provider"], c["key"]), "answer_url": url, "answer_body": body}
    if k == "build":
        url, body = m.build_request(c["provider"], c["model"], c["instructions"], c["text"], c["images"], c["max_tokens"], c["effort"])
        return [url, body]
    raise ValueError(k)


print(json.dumps([run(c) for c in json.load(sys.stdin)]))
