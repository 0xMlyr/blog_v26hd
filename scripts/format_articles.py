#!/usr/bin/env python3
"""Format extracted blog articles with the DeepSeek API.

The full prompt is sent with every request, followed by the target article
JSON. Only the formatted article is kept in the output JSON.

Outputs:
    blog_data/formatted/articles.json    [{id, article}, ...]
    blog_data/formatted/progress.jsonl   one record per article
    blog_data/formatted/report.json      success/failure/validation stats

Usage:
    python scripts/format_articles.py --dry-run
    python scripts/format_articles.py --ids E23001-Z0S,E23006-C0A,E25059-C0A
    python scripts/format_articles.py --limit 10
    python scripts/format_articles.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MAX_TOKENS = 8192
DEFAULT_TIMEOUT = 240
DEFAULT_RETRIES = 3


class ApiError(Exception):
    def __init__(self, status: int, data: Any, retryable: bool):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.data = data
        self.retryable = retryable


def read_token(path: Path) -> str:
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        raise ValueError(f"API token file is empty: {path}")

    for line in raw.splitlines():
        token = line.strip()
        if not token:
            continue
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        token = token.strip().strip('"').strip("'")
        if token:
            return token

    raise ValueError(f"No token found in: {path}")


def load_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.find("【System Prompt】")
    end = text.find("第三部分：工程侧校验与统计")

    if start != -1 and end != -1 and end > start:
        text = text[start:end]

    prompt = text.strip()
    if not prompt:
        raise ValueError(f"Prompt file is empty: {path}")
    return prompt


def build_messages(prompt: str, article: dict[str, Any]) -> list[dict[str, str]]:
    user_content = json.dumps(
        {
            "id": article["id"],
            "date": article["date"],
            "content": article["content"],
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": user_content},
    ]


def call_api(
    token: str,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    timeout: int,
) -> dict[str, Any]:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {"raw": body}
        retryable = error.code in {408, 429, 500, 502, 503, 504}
        raise ApiError(error.code, data, retryable) from error
    except urllib.error.URLError as error:
        raise ApiError(0, {"error": str(error.reason)}, True) from error
    except TimeoutError as error:
        raise ApiError(0, {"error": "request timed out"}, True) from error
    except json.JSONDecodeError as error:
        raise ApiError(0, {"error": f"invalid JSON response: {error}"}, True) from error


def extract_choice(data: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("API response has no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    content = message.get("content") or ""
    finish_reason = choice.get("finish_reason")
    usage = data.get("usage") or {}
    return content, finish_reason, usage


def parse_article_content(content: str) -> tuple[str | None, str | None]:
    text = content.strip()
    if not text:
        return None, "empty content"

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict) and isinstance(data.get("article"), str):
            return data["article"], None
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start != -1:
        try:
            data, _index = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(data, dict) and isinstance(data.get("article"), str):
                return data["article"], None
        except json.JSONDecodeError:
            pass

    return None, "response is not a JSON object with a string article field"


def strip_for_validation(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"(?m)^[ \t]*#{1,6}[ \t]*", "", text)
    text = re.sub(r"(?m)^[ \t]*>[ \t]?", "", text)
    text = re.sub(r"(?m)^[ \t]*(?:[-*+]|\d+\.)[ \t]+", "", text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.M)
    text = text.replace("*", "").replace("_", "").replace("`", "")
    text = re.sub(r"\s+", "", text)
    return text


def is_formula_like(original: str, article: str) -> bool:
    if "$" in article:
        return True
    return bool(
        re.search(
            r"(?m)^[ \t]*(?:P\(|lim\b|=|\\frac|\\sum|\\int|N\s*→∞|\d+\s*/\s*\d+)",
            original,
        )
    )


def is_poem_like(original: str) -> bool:
    lines = [line.strip() for line in original.splitlines() if line.strip()]
    if len(lines) < 6:
        return False
    average_length = sum(len(line) for line in lines) / len(lines)
    return average_length <= 30


def parse_nature(article_id: str) -> str | None:
    match = re.match(r"^[EF]\d{5}-([CZ])([0X])([ASU])", article_id)
    return match.group(2) if match else None


def validate_article(original: str, article: str) -> tuple[bool, str]:
    if is_formula_like(original, article):
        return True, "formula-like: character validation skipped; manual review recommended"

    original_normalized = strip_for_validation(original)
    article_normalized = strip_for_validation(article)

    if original_normalized == article_normalized:
        return True, "content characters preserved"

    limit = min(len(original_normalized), len(article_normalized))
    first_diff = limit
    for index in range(limit):
        if original_normalized[index] != article_normalized[index]:
            first_diff = index
            break

    original_snippet = original_normalized[max(0, first_diff - 20): first_diff + 40]
    article_snippet = article_normalized[max(0, first_diff - 20): first_diff + 40]
    return False, (
        f"content mismatch at normalized char {first_diff}: "
        f"original={original_snippet!r} output={article_snippet!r}"
    )


def load_progress(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records[record["id"]] = record
    return records


def write_artifacts(
    output_dir: Path,
    input_articles: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    articles = []
    for article in input_articles:
        record = records.get(article["id"])
        if record and record.get("status") == "success" and isinstance(record.get("article"), str):
            articles.append({"id": article["id"], "article": record["article"]})

    (output_dir / "articles.json").write_text(
        json.dumps(articles, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    ordered_records = [
        records[article["id"]]
        for article in input_articles
        if article["id"] in records
    ]
    (output_dir / "progress.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in ordered_records) + "\n",
        encoding="utf-8",
    )

    success = sum(1 for record in ordered_records if record.get("status") == "success")
    failed = sum(1 for record in ordered_records if record.get("status") == "failed")
    pending = len(input_articles) - len(ordered_records)
    validation_failures = [
        record["id"]
        for record in ordered_records
        if record.get("status") == "success" and not record.get("validation_ok", True)
    ]

    x_ids = {article["id"] for article in input_articles if parse_nature(article["id"]) == "X"}
    x_success = sum(
        1
        for record in ordered_records
        if record["id"] in x_ids and record.get("status") == "success"
    )
    x_failed = sum(
        1
        for record in ordered_records
        if record["id"] in x_ids and record.get("status") == "failed"
    )

    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "reasoning_tokens": 0}
    for record in ordered_records:
        usage = record.get("usage") or {}
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            usage_totals[key] += int(usage.get(key) or 0)
        details = usage.get("completion_tokens_details") or {}
        usage_totals["reasoning_tokens"] += int(details.get("reasoning_tokens") or 0)

    report = {
        "input_total": len(input_articles),
        "processed": len(ordered_records),
        "success": success,
        "failed": failed,
        "pending": pending,
        "x_total": len(x_ids),
        "x_success": x_success,
        "x_failed": x_failed,
        "formula_like_success": sum(
            1 for record in ordered_records if record.get("formula_like") and record.get("status") == "success"
        ),
        "poem_like_success": sum(
            1 for record in ordered_records if record.get("poem_like") and record.get("status") == "success"
        ),
        "validation_failures": validation_failures,
        "validation_failure_count": len(validation_failures),
        "usage_totals": usage_totals,
        "failed_ids": [
            record["id"]
            for record in ordered_records
            if record.get("status") == "failed"
        ],
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Format extracted articles with DeepSeek")
    parser.add_argument("--input", default=str(project_root / "blog_data" / "extracted" / "articles.json"))
    parser.add_argument("--output-dir", default=str(project_root / "blog_data" / "formatted"))
    parser.add_argument(
        "--prompt-file",
        default=str(project_root / "blog_data" / "llm-format-prompt-final.txt"),
    )
    parser.add_argument("--api-file", default=str(project_root / "blog_data" / "api.txt"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", default=None, help="comma-separated article IDs")
    parser.add_argument("--no-resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    output_dir = Path(args.output_dir).resolve()
    prompt_path = Path(args.prompt_file).resolve()
    token_path = Path(args.api_file).resolve()

    input_articles = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(input_articles, list):
        print("INPUT_ERROR: articles.json must be a list", file=sys.stderr)
        return 2

    if args.ids:
        selected = {item.strip() for item in args.ids.split(",") if item.strip()}
        input_articles = [article for article in input_articles if article.get("id") in selected]
    if args.limit is not None:
        input_articles = input_articles[: args.limit]

    try:
        prompt = load_prompt(prompt_path)
    except Exception as error:
        print(f"PROMPT_ERROR: {error}", file=sys.stderr)
        return 2

    print(f"model:        {args.model}")
    print(f"input:        {input_path}")
    print(f"articles:     {len(input_articles)}")
    print(f"prompt_chars: {len(prompt)}")
    print(f"output_dir:   {output_dir}")

    if args.dry_run:
        print("DRY_RUN: no API requests sent")
        return 0

    try:
        token = read_token(token_path)
    except Exception as error:
        print(f"TOKEN_ERROR: {error}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "progress.jsonl"
    records = {} if args.no_resume else load_progress(progress_path)

    if records:
        print(f"resume:       loaded {len(records)} existing record(s)")
        if not args.no_resume and args.ids is None and args.limit is None:
            input_articles = [
                article
                for article in input_articles
                if not (
                    records.get(article["id"], {}).get("status") == "success"
                )
            ]
            print(f"to_process:   {len(input_articles)} (successful records skipped)")

    if not input_articles:
        print("nothing to process")
        write_artifacts(output_dir, json.loads(input_path.read_text(encoding="utf-8")), records)
        return 0

    fatal_error = False

    for index, article in enumerate(input_articles, 1):
        article_id = article["id"]
        date = article["date"]
        original = article["content"]
        formula_like = is_formula_like(original, "")
        poem_like = is_poem_like(original)
        nature = parse_nature(article_id)
        x_sensitive = nature == "X"

        print(
            f"[{index}/{len(input_articles)}] {article_id} | {date} | "
            f"{len(original)} chars | X={x_sensitive} | formula={formula_like} | poem={poem_like}"
        )

        messages = build_messages(prompt, article)
        max_tokens = args.max_tokens
        attempts = 0
        success_record: dict[str, Any] | None = None
        last_error = ""

        while attempts < args.retries:
            attempts += 1
            try:
                data = call_api(
                    token=token,
                    base_url=args.base_url,
                    model=args.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    timeout=args.timeout,
                )
                content, finish_reason, usage = extract_choice(data)
                parsed_article, parse_error = parse_article_content(content)

                if parse_error:
                    last_error = parse_error
                    print(f"  attempt {attempts}: parse error: {parse_error}")
                    if finish_reason == "length":
                        max_tokens = min(max_tokens * 2, 16384)
                        print(f"  increasing max_tokens to {max_tokens} after length finish")
                    if attempts < args.retries and content:
                        messages = messages + [
                            {"role": "assistant", "content": content},
                            {
                                "role": "user",
                                "content": '上一次输出不是合法 JSON。请只返回 {"article":"..."}，不要解释。',
                            },
                        ]
                    time.sleep(args.sleep)
                    continue

                validation_ok, validation_message = validate_article(original, parsed_article)
                formula_like = is_formula_like(original, parsed_article)
                poem_like = is_poem_like(original)

                success_record = {
                    "id": article_id,
                    "date": date,
                    "status": "success",
                    "article": parsed_article,
                    "error": None,
                    "attempts": attempts,
                    "finish_reason": finish_reason,
                    "usage": usage,
                    "validation_ok": validation_ok,
                    "validation_message": validation_message,
                    "formula_like": formula_like,
                    "poem_like": poem_like,
                    "x_sensitive": x_sensitive,
                    "timestamp": now_iso(),
                }
                records[article_id] = success_record
                print(
                    f"  success: {len(parsed_article)} chars | "
                    f"finish={finish_reason} | validation_ok={validation_ok}"
                )
                break

            except ApiError as error:
                last_error = json.dumps(error.data, ensure_ascii=False)[:800]
                print(f"  attempt {attempts}: API error status={error.status} retryable={error.retryable}")
                if not error.retryable:
                    fatal_error = error.status in {401, 403}
                    break
                if attempts < args.retries:
                    time.sleep(min(2 ** attempts, 20))
                    continue
                break
            except Exception as error:
                last_error = f"{type(error).__name__}: {error}"
                print(f"  attempt {attempts}: unexpected error: {last_error}")
                if attempts < args.retries:
                    time.sleep(min(2 ** attempts, 20))
                    continue
                break

        if success_record is None:
            records[article_id] = {
                "id": article_id,
                "date": date,
                "status": "failed",
                "error": last_error,
                "attempts": attempts,
                "formula_like": formula_like,
                "poem_like": poem_like,
                "x_sensitive": x_sensitive,
                "timestamp": now_iso(),
            }
            print(f"  FAILED: {last_error[:200]}")

        write_artifacts(output_dir, json.loads(input_path.read_text(encoding="utf-8")), records)
        time.sleep(args.sleep)

        if fatal_error:
            print("FATAL: authentication/configuration error; stopping", file=sys.stderr)
            break

    final_articles = json.loads(input_path.read_text(encoding="utf-8"))
    write_artifacts(output_dir, final_articles, records)

    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    print("summary:")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
