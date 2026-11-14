#!/usr/bin/env python3
"""Build the final KV bulk-upload JSON from extracted dates and formatted articles.

Input:
    blog_data/extracted/articles.json   [{id, date, content}, ...]
    blog_data/formatted/articles.json   [{id, article}, ...]

Output:
    blog_data/final/kv-bulk.json        [{key, value}, ...]
        value is a JSON string:
        {"metadata": {"date": "YYYY-MM-DD"}, "article": "..."}

This is the format expected by:
    wrangler kv bulk put <file> --binding BLOG_KV --remote
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Build KV bulk JSON")
    parser.add_argument(
        "--extracted",
        default=str(root / "blog_data" / "extracted" / "articles.json"),
    )
    parser.add_argument(
        "--formatted",
        default=str(root / "blog_data" / "formatted" / "articles.json"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(root / "blog_data" / "final"),
    )
    args = parser.parse_args()

    extracted = json.loads(Path(args.extracted).read_text(encoding="utf-8"))
    formatted = json.loads(Path(args.formatted).read_text(encoding="utf-8"))

    dates = {article["id"]: article["date"] for article in extracted}
    if len(dates) != len(extracted):
        print("ERROR: duplicate IDs in extracted articles", file=sys.stderr)
        return 2

    records = []
    errors = []
    seen = set()

    for article in formatted:
        article_id = article.get("id")
        markdown = article.get("article")

        if not isinstance(article_id, str) or not article_id:
            errors.append(f"invalid id: {article!r}")
            continue
        if article_id in seen:
            errors.append(f"duplicate formatted id: {article_id}")
            continue
        if article_id not in dates:
            errors.append(f"missing date for id: {article_id}")
            continue
        if not isinstance(markdown, str) or not markdown.strip():
            errors.append(f"empty article: {article_id}")
            continue

        seen.add(article_id)
        value_object = {
            "metadata": {"date": dates[article_id]},
            "article": markdown,
        }
        records.append(
            {
                "key": article_id,
                "value": json.dumps(value_object, ensure_ascii=False),
            }
        )

    missing = sorted(set(dates) - seen)
    if missing:
        errors.extend(f"formatted article missing for id: {article_id}" for article_id in missing)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bulk_path = output_dir / "kv-bulk.json"
    bulk_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Validate the generated file itself.
    validation_errors = []
    if len(records) != len(extracted):
        validation_errors.append(
            f"record count mismatch: records={len(records)} extracted={len(extracted)}"
        )

    for record in records:
        try:
            value = json.loads(record["value"])
        except json.JSONDecodeError as error:
            validation_errors.append(f"{record['key']}: value is not valid JSON: {error}")
            continue
        if sorted(value.keys()) != ["article", "metadata"]:
            validation_errors.append(f"{record['key']}: unexpected value keys {sorted(value.keys())}")
        if sorted(value.get("metadata", {}).keys()) != ["date"]:
            validation_errors.append(f"{record['key']}: metadata must contain only date")
        if not isinstance(value.get("article"), str) or not value["article"].strip():
            validation_errors.append(f"{record['key']}: article is empty")

    all_errors = errors + validation_errors
    report_lines = [
        "KV final JSON report",
        "=" * 60,
        f"extracted articles: {len(extracted)}",
        f"formatted articles: {len(formatted)}",
        f"kv records: {len(records)}",
        f"date range: {min(dates.values())} .. {max(dates.values())}",
        f"output: {bulk_path}",
        f"errors: {len(all_errors)}",
        "",
        "Value shape:",
        '  {"metadata": {"date": "YYYY-MM-DD"}, "article": "Markdown"}',
        "",
        "Sample key/date:",
    ]
    for record in records[:5]:
        value = json.loads(record["value"])
        report_lines.append(f"  {record['key']} -> {value['metadata']['date']}")
    if all_errors:
        report_lines.append("")
        report_lines.append("Errors:")
        report_lines.extend(f"  {error}" for error in all_errors)

    report_path = output_dir / "kv-report.txt"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "extracted": len(extracted),
        "formatted": len(formatted),
        "kv_records": len(records),
        "errors": len(all_errors),
        "output": str(bulk_path),
    }, ensure_ascii=False))

    if all_errors:
        for error in all_errors[:20]:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
