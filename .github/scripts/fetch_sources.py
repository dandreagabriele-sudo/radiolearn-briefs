"""
fetch_sources.py — Fetcher invoked by GitHub Actions cron sabato 13:30 UTC.

Steps:
  1. Cadence check: if last brief was generated <13 days ago, skip silently
     (idempotent: routine sees old candidates.json untouched, won't generate)
  2. Fetch all 7 sources via briefs_lib
  3. Dedup + filter against processed_dois in state
  4. Optional PMC enrichment for top candidates
  5. Commit inbox/candidates.json with the fresh candidates list

The routine cloud (scheduled 30 min later) reads this file and does curation.

Env vars: GH_TOKEN, GH_REPO, NCBI_API_KEY, NCBI_EMAIL
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import briefs_lib as lib

GH_TOKEN = os.environ["GH_TOKEN"]
GH_REPO = os.environ["GH_REPO"]
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "")
NOW = datetime.now(timezone.utc)

lib.init_briefs(GH_TOKEN, GH_REPO, NCBI_API_KEY, NCBI_EMAIL)


# ─── 1. Cadence check ──────────────────────────────────────────────
print("─" * 60)
print("Phase 1: cadence check")

state_content, _ = lib.gh_get("state.json")
if state_content:
    state = json.loads(state_content)
else:
    state = {"last_brief_at": None, "processed_dois": [], "briefs_archive": []}

last = state.get("last_brief_at")
if last:
    last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    days_since = (NOW - last_dt).days
    if days_since < 13:
        print(f"  → only {days_since} days since last brief; exiting without staging")
        sys.exit(0)

print("  → proceeding to fetch sources")


# ─── 2. Fetch sources ──────────────────────────────────────────────
print("─" * 60)
print("Phase 2: fetching sources")

candidates = []
sources_status = {}


def safe_fetch(label, fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        sources_status[label] = "ok"
        print(f"  ✓ {label}: {len(result)} items")
        return result
    except Exception as e:
        sources_status[label] = f"failed: {type(e).__name__}"
        print(f"  ✗ {label}: {e}")
        return []


for focus in ["ild_clinica", "ild_imaging", "cardio_imaging"]:
    candidates.extend(safe_fetch(f"pubmed_{focus}", lib.fetch_pubmed, focus, days_back=14))

for focus in ["ild_imaging", "cardio_imaging"]:
    candidates.extend(safe_fetch(f"arxiv_{focus}", lib.fetch_arxiv, focus, days_back=14))

candidates.extend(safe_fetch("medrxiv", lib.fetch_medrxiv, days_back=14))

for url, label, focus in lib.RSS_FEEDS:
    candidates.extend(safe_fetch(f"rss_{label}", lib.fetch_rss, url, label, focus, days_back=14))

guidelines = safe_fetch("guidelines", lib.fetch_guideline_pages, days_back=14)
industry = safe_fetch("industry", lib.fetch_industry_rss, days_back=14)


# Health check: abort if >50% of sources failed
total_sources = len(sources_status)
failed = [k for k, v in sources_status.items() if v != "ok"]
fail_ratio = len(failed) / max(total_sources, 1)
print(f"\nSources health: {total_sources - len(failed)}/{total_sources} ok ({fail_ratio:.0%} failure rate)")

if fail_ratio > 0.5:
    print(f"✗ Aborting: >50% source failures. {failed}")
    sys.exit(1)


# ─── 3. Dedup + filter ─────────────────────────────────────────────
print("─" * 60)
print("Phase 3: dedup + filter already-processed")

candidates = lib.dedup_papers(candidates)
processed_set = set((d or "").lower() for d in state.get("processed_dois", []))
fresh = [p for p in candidates if not (p.doi and p.doi.lower() in processed_set)]

print(f"  → {len(candidates)} after dedup, {len(fresh)} after dropping processed DOIs")

if len(fresh) < 3:
    print(f"⚠ Too few fresh candidates ({len(fresh)}); exiting without staging")
    sys.exit(1)


# ─── 4. PMC enrichment (best effort, top 20) ───────────────────────
print("─" * 60)
print("Phase 4: PMC enrichment (best effort)")

enriched = 0
for paper in fresh[:20]:
    if paper.pmcid:
        try:
            if lib.enrich_pmc_fulltext(paper):
                enriched += 1
        except Exception:
            pass
print(f"  → {enriched} papers enriched with PMC fulltext")


# ─── 5. Stage candidates.json in inbox ─────────────────────────────
print("─" * 60)
print("Phase 5: staging candidates.json in inbox")

week_iso = f"{NOW.isocalendar().year}-W{NOW.isocalendar().week:02d}"
payload = {
    "version": "1.0",
    "staged_at": NOW.isoformat(),
    "week_iso": week_iso,
    "date_it": NOW.strftime("%-d ") + ["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"][NOW.month-1] + NOW.strftime(" %Y"),
    "candidates_count": len(fresh),
    "days_back": 14,
    "sources_status": sources_status,
    "candidates": [
        {
            "title": p.title,
            "authors": p.authors[:5],
            "journal": p.journal,
            "date": p.publication_date,
            "doi": p.doi,
            "pmid": p.pmid,
            "pmcid": p.pmcid,
            "url": p.url,
            "source": p.source,
            "focus": p.focus_hint,
            "open_access": p.open_access,
            "abstract": (p.abstract or "")[:2000],
            "has_fulltext": bool(p.fulltext),
            "fulltext": (p.fulltext or "")[:5000] if p.fulltext else "",
        }
        for p in fresh
    ],
    "guidelines": [d.to_dict() for d in guidelines],
    "industry": [d.to_dict() for d in industry],
}

candidates_path = "inbox/candidates.json"
existing, existing_sha = lib.gh_get(candidates_path)
lib.gh_put(
    candidates_path,
    json.dumps(payload, indent=2, ensure_ascii=False),
    f"Stage candidates {week_iso} ({len(fresh)} fresh)",
    sha=existing_sha if existing else None,
)
print(f"  ✓ committed inbox/candidates.json ({len(fresh)} candidates)")

print("─" * 60)
print(f"✅ Fetch complete for {week_iso}. Routine will pick up at 14:00 CEST.")
