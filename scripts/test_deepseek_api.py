#!/usr/bin/env python3
"""Minimal DeepSeek API connectivity test.

Reads the API token from blog_data/api.txt by default.
The token is never printed.

Usage:
    python scripts/test_deepseek_api.py
    python scripts/test_deepseek_api.py --api-file blog_data/api.txt
    python scripts/test_deepseek_api.py --base-url https://api.deepseek.com/v1
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_MODEL = "V4.1FLASH1017"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def read_token(path: Path) -> str:
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        raise ValueError(f"API token file is empty: {path}")

    token = ""
    for line in raw.splitlines():
        candidate = line.strip()
        if candidate:
            token = candidate
            break

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    token = token.strip().strip('"').strip("'")
    if not token:
        raise ValueError(f"No token found in: {path}")
    return token


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Test DeepSeek API availability")
    parser.add_argument(
        "--api-file",
        default=str(project_root / "blog_data" / "api.txt"),
        help="path to the token file (default: blog_data/api.txt)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="model name")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument("--timeout", type=int, default=60, help="request timeout in seconds")
    parser.add_argument("--max-tokens", type=int, default=256, help="max completion tokens")
    parser.add_argument(
        "--prompt",
        default="只回复 OK",
        help="minimal prompt used for the connectivity test",
    )
    args = parser.parse_args()

    token_path = Path(args.api_file).resolve()
    try:
        token = read_token(token_path)
    except Exception as error:
        print(f"TOKEN_ERROR: {error}", file=sys.stderr)
        return 2

    endpoint = args.base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You are a connectivity test. Reply with OK only."},
            {"role": "user", "content": args.prompt},
        ],
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "stream": False,
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    print(f"endpoint: {endpoint}")
    print(f"model:    {args.model}")
    print(f"token:    loaded from {token_path.name} ({len(token)} chars, not printed)")

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        status = error.code
        body = error.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        print(f"NETWORK_ERROR: {error.reason}", file=sys.stderr)
        return 3
    except TimeoutError:
        print("NETWORK_ERROR: request timed out", file=sys.stderr)
        return 3

    print(f"http_status: {status}")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        print(f"raw_response: {body[:1000]}", file=sys.stderr)
        return 1

    if status >= 400:
        error = data.get("error") if isinstance(data, dict) else None
        message = error.get("message") if isinstance(error, dict) else str(error)
        print(f"API_ERROR: {message}", file=sys.stderr)
        print(f"raw_response: {json.dumps(data, ensure_ascii=False)[:1500]}", file=sys.stderr)
        return 1

    choices = data.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        content = message.get("content", "")

    print(f"response_model: {data.get('model')}")
    print(f"content: {content!r}")
    print(f"usage: {json.dumps(data.get('usage'), ensure_ascii=False)}")
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
