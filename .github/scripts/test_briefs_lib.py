"""
test_briefs_lib.py — Diagnostic harness for briefs_lib.py

Runs each fetcher in isolation, prints clear pass/fail with sample data.
Designed for visual inspection of GitHub Actions logs.

Usage (from workflow):
  SOURCES=pubmed,arxiv python3 test_briefs_lib.py
  SOURCES=all DAYS_BACK=10 python3 test_briefs_lib.py
"""
import os
import sys
import json
import traceback

# Make sure we can import briefs_lib from the same folder
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import briefs_lib as lib

DAYS_BACK = int(os.environ.get("DAYS_BACK", "10"))
SOURCES = os.environ.get("SOURCES", "all").lower().split(",")
SOURCES = [s.strip() for s in SOURCES]
RUN_ALL = "all" in SOURCES


def header(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def show_sample(items, n=3):
    """Print the first N items in compact form."""
    for i, item in enumerate(items[:n], 1):
        if hasattr(item, "to_dict"):
            d = item.to_dict()
            title = d.get("title", "")[:120]
            journal = d.get("journal", d.get("source", ""))
            date = d.get("publication_date") or d.get("date_seen", "")
            print(f"  {i}. [{date}] {title}")
            print(f"     {journal} · DOI: {d.get('doi', '—')}")
        else:
            print(f"  {i}. {item}")


def section(name, fn, *args, **kwargs):
    """Run a single test section with timing and error handling."""
    header(name)
    try:
        results = fn(*args, **kwargs)
        n = len(results)
        print(f"  ✓ Returned {n} items")
        if n:
            show_sample(results)
        else:
            print(f"  ⚠ EMPTY result — check if query/source is correct")
        return results
    except Exception as e:
        print(f"  ✗ FAILED with exception: {e}")
        traceback.print_exc()
        return None


def main():
    # Init: we use a dummy GitHub config because tests don't need GH access.
    # Pass the real NCBI key/email so PubMed/PMC requests are authenticated.
    lib.init_briefs(
        gh_token="dummy",  # not used by fetchers
        gh_repo="dandreagabriele-sudo/radiolearn-briefs",
        ncbi_api_key=os.environ.get("NCBI_API_KEY", ""),
        ncbi_email=os.environ.get("NCBI_EMAIL", ""),
    )

    print(f"\n{'#' * 70}")
    print(f"# briefs_lib diagnostic suite")
    print(f"# Days back: {DAYS_BACK}")
    print(f"# Sources to test: {', '.join(SOURCES)}")
    print(f"# NCBI API key: {'set' if os.environ.get('NCBI_API_KEY') else 'NOT set'}")
    print(f"# NCBI email: {'set' if os.environ.get('NCBI_EMAIL') else 'NOT set'}")
    print(f"{'#' * 70}")

    summary = {}

    # ─── PubMed ───────────────────────────────────────────────────
    if RUN_ALL or "pubmed" in SOURCES:
        for focus in ["ild_clinica", "ild_imaging", "cardio_imaging"]:
            r = section(f"PubMed — {focus}",
                       lib.fetch_pubmed, focus, days_back=DAYS_BACK)
            summary[f"pubmed_{focus}"] = len(r) if r is not None else "ERR"

    # ─── arXiv ────────────────────────────────────────────────────
    if RUN_ALL or "arxiv" in SOURCES:
        for focus in ["ild_imaging", "cardio_imaging"]:
            r = section(f"arXiv — {focus}",
                       lib.fetch_arxiv, focus, days_back=DAYS_BACK)
            summary[f"arxiv_{focus}"] = len(r) if r is not None else "ERR"

    # ─── medRxiv ──────────────────────────────────────────────────
    if RUN_ALL or "medrxiv" in SOURCES:
        r = section("medRxiv — all focuses (filtered by keyword)",
                   lib.fetch_medrxiv, days_back=DAYS_BACK)
        summary["medrxiv"] = len(r) if r is not None else "ERR"

    # ─── RSS feeds ────────────────────────────────────────────────
    if RUN_ALL or "rss" in SOURCES:
        for url, label, focus in lib.RSS_FEEDS:
            r = section(f"RSS — {label}",
                       lib.fetch_rss, url, label, focus, days_back=DAYS_BACK)
            summary[f"rss_{label}"] = len(r) if r is not None else "ERR"

    # ─── Guidelines ───────────────────────────────────────────────
    if RUN_ALL or "guidelines" in SOURCES:
        r = section("Guideline pages (scrape)",
                   lib.fetch_guideline_pages, days_back=14)
        summary["guidelines"] = len(r) if r is not None else "ERR"

    # ─── Industry ─────────────────────────────────────────────────
    if RUN_ALL or "industry" in SOURCES:
        r = section("Industry sources",
                   lib.fetch_industry_rss, days_back=14)
        summary["industry"] = len(r) if r is not None else "ERR"

    # ─── Summary ──────────────────────────────────────────────────
    header("SUMMARY")
    for k, v in summary.items():
        marker = "✓" if isinstance(v, int) and v > 0 else ("⚠" if v == 0 else "✗")
        print(f"  {marker} {k}: {v}")

    print(f"\n{'#' * 70}")
    print("# Diagnostic complete. Review counts above.")
    print(f"{'#' * 70}\n")


if __name__ == "__main__":
    main()
