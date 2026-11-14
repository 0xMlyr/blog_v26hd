#!/usr/bin/env python3
"""Extract [INFO]/[DATE]/[CONTENT] article blocks from the blog PDF.

Output fields per article:
    id       original [INFO] value
    date     normalized YYYY-MM-DD
    content  raw text between [CONTENT] and [END], page footers removed

Requires PyMuPDF:
    python -m pip install PyMuPDF
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import statistics
from pathlib import Path

import pymupdf


FOOTER_RE = re.compile(r"^\s*第\s*(\d+)\s*页\s*共\s*(\d+)\s*页\s*$")
DATE_RE = re.compile(r"^(\d{4})\s+(\d{1,2})\s+(\d{1,2})(?:\.\((\d{2}):(\d{2})\))?$")


def clean_page_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if FOOTER_RE.match(line):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines)


def normalize_date(raw: str) -> tuple[str, bool]:
    match = DATE_RE.match(raw.strip())
    if not match:
        return raw.strip(), False
    year, month, day, _hour, _minute = match.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}", True


def parse_entries(pdf_path: Path):
    doc = pymupdf.open(pdf_path)
    page_texts = []
    entries = []
    current = None
    seen_info = False

    for page_number, page in enumerate(doc, 1):
        text = clean_page_text(page.get_text("text"))
        page_texts.append(text)

        for line in text.splitlines():
            stripped = line.strip()

            if stripped.startswith("[INFO]"):
                seen_info = True
                if current is not None:
                    entries.append(current)
                current = {
                    "id": stripped[len("[INFO]"):].strip(),
                    "date_raw": "",
                    "content_lines": [],
                    "start_page": page_number,
                    "end_page": page_number,
                    "ended": False,
                }
            elif stripped.startswith("[DATE]") and current is not None:
                current["date_raw"] = stripped[len("[DATE]"):].strip()
            elif stripped.startswith("[CONTENT]") and current is not None:
                rest = stripped[len("[CONTENT]"):].strip()
                if rest:
                    current["content_lines"].append(rest)
            elif stripped.startswith("[END]") and current is not None:
                current["ended"] = True
                current["end_page"] = page_number
                entries.append(current)
                current = None
            elif current is not None:
                current["content_lines"].append(line.rstrip())
            elif not seen_info:
                # The first page is a legend, not an article.
                continue

    if current is not None:
        entries.append(current)

    return doc, page_texts, entries


def build_articles(entries):
    articles = []
    invalid_dates = []
    missing_end = []
    empty_content = []
    page_spanning = 0
    duplicate_ids = {}

    for entry in entries:
        date, date_ok = normalize_date(entry["date_raw"])
        content = "\n".join(entry["content_lines"]).strip()

        if not date_ok:
            invalid_dates.append(entry["id"])
        if not entry["ended"]:
            missing_end.append(entry["id"])
        if not content:
            empty_content.append(entry["id"])
        if entry["end_page"] > entry["start_page"]:
            page_spanning += 1

        articles.append(
            {
                "id": entry["id"],
                "date": date,
                "content": content,
            }
        )

    id_counter = collections.Counter(article["id"] for article in articles)
    duplicate_ids = {key: value for key, value in id_counter.items() if value > 1}

    return articles, {
        "invalid_dates": invalid_dates,
        "missing_end": missing_end,
        "empty_content": empty_content,
        "page_spanning": page_spanning,
        "duplicate_ids": duplicate_ids,
    }


def write_notes(pdf_path: Path, page_texts, output_path: Path) -> None:
    first_page = page_texts[0].strip() if page_texts else ""
    second_collection_cover = ""

    for index, text in enumerate(page_texts[1:], 2):
        if "第二缉" in text:
            marker = text.find("[INFO]")
            second_collection_cover = text if marker == -1 else text[:marker]
            second_collection_cover = second_collection_cover.strip()
            break

    sections = [
        f"Source PDF: {pdf_path}",
        "",
        "===== PAGE 1 LEGEND =====",
        first_page,
        "",
        "===== SECOND COLLECTION COVER =====",
        second_collection_cover,
        "",
    ]
    output_path.write_text("\n".join(sections), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pdf",
        nargs="?",
        default=str(Path(__file__).resolve().parents[1] / "blog_data" / "文集2_smallpdf.pdf"),
        help="path to the source PDF",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="output directory (default: blog_data/extracted next to the PDF)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else pdf_path.parent / "extracted"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    doc, page_texts, entries = parse_entries(pdf_path)
    articles, report = build_articles(entries)

    dates = [article["date"] for article in articles if article["date"]]
    content_lengths = [len(article["content"]) for article in articles]

    json_path = output_dir / "articles.json"
    jsonl_path = output_dir / "articles.jsonl"
    report_path = output_dir / "extraction-report.txt"
    notes_path = output_dir / "notes.txt"

    json_path.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    jsonl_path.write_text(
        "\n".join(json.dumps(article, ensure_ascii=False) for article in articles) + "\n",
        encoding="utf-8",
    )
    write_notes(pdf_path, page_texts, notes_path)

    report_lines = [
        "PDF extraction report",
        "=" * 60,
        f"source: {pdf_path}",
        f"pages: {len(page_texts)}",
        f"articles: {len(articles)}",
        f"date range: {min(dates) if dates else None} .. {max(dates) if dates else None}",
        f"page-spanning entries: {report['page_spanning']}",
        f"invalid dates: {len(report['invalid_dates'])} {report['invalid_dates']}",
        f"missing [END]: {len(report['missing_end'])} {report['missing_end']}",
        f"empty content: {len(report['empty_content'])} {report['empty_content']}",
        f"duplicate ids: {report['duplicate_ids']}",
        f"content chars: min={min(content_lengths)} max={max(content_lengths)} "
        f"mean={round(statistics.mean(content_lengths), 1)} "
        f"median={statistics.median(content_lengths)}",
        "",
        "outputs:",
        f"  {json_path}",
        f"  {jsonl_path}",
        f"  {notes_path}",
        f"  {report_path}",
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "pages": len(page_texts),
        "articles": len(articles),
        "date_range": [min(dates) if dates else None, max(dates) if dates else None],
        "page_spanning": report["page_spanning"],
        "invalid_dates": len(report["invalid_dates"]),
        "missing_end": len(report["missing_end"]),
        "empty_content": len(report["empty_content"]),
        "duplicate_ids": len(report["duplicate_ids"]),
        "output_dir": str(output_dir),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
