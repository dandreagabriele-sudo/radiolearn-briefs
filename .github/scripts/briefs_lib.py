"""
briefs_lib.py — RadioLearn Briefs library

Imported by the weekly Cloud Routine at bootstrap via exec(). Exposes:
  - Source fetchers for 6 different sources
  - Full text enrichment
  - GitHub Contents API helpers (mirror of radiolearn-state pattern)
  - Paper/Document dataclasses

Public API (call init_briefs() first, then any fetcher):

  Bootstrap:
    init_briefs(gh_token, gh_repo, ncbi_api_key, ncbi_email, ncbi_tool)

  Sources (each returns list[Paper] or list[Document]):
    fetch_pubmed(focus_name, days_back=10)
    fetch_arxiv(focus_name, days_back=10)
    fetch_medrxiv(days_back=10)
    fetch_rss(feed_url, source_label, focus_hint, days_back=10)
    fetch_guideline_pages(days_back=14)
    fetch_industry_rss(days_back=14)

  Enrichment:
    enrich_pmc_fulltext(paper)  -> bool (mutates paper, adds .fulltext if available)
    enrich_preprint_fulltext(paper) -> bool (mutates paper, downloads PDF if pypdf available)

  GitHub Contents API:
    gh_get(path)               -> (content_str, sha) | (None, None)
    gh_list(folder)            -> list of items
    gh_put(path, content, msg, sha=None) -> new_sha
    gh_delete(path, sha, msg)

  Helpers:
    Paper, Document          (dataclasses)
    dedup_papers(papers)     -> list[Paper] (deduplicates by DOI/title)

CONFIGURATION (queries) is in the top section — edit there to tweak focus.
"""

import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from html import unescape
from typing import Optional


# ════════════════════════════════════════════════════════════════════
# CONFIGURATION — EDIT HERE TO TUNE FOCUS QUERIES
# ════════════════════════════════════════════════════════════════════

# PubMed queries: one per focus area. Use PubMed search syntax.
# The date range is added programmatically by fetch_pubmed().
PUBMED_QUERIES = {
    # ─────────────────────────────────────────────────────────────
    # Focus 1: ILD clinica
    # Backbone ontologico: ATS/ERS 2025 update of IIP classification
    # (European Respiratory Journal, December 2025).
    # Covers all major interstitial patterns + alveolar filling
    # disorders + secondary ILDs (CTDs, sarcoid, drug-induced).
    # ─────────────────────────────────────────────────────────────
    "ild_clinica": (
        '('
        # MeSH core ILD
        '"Idiopathic Pulmonary Fibrosis"[MeSH Terms] OR '
        '"Hypersensitivity Pneumonitis"[MeSH Terms] OR '
        '"Lung Diseases, Interstitial"[MeSH Terms] OR '
        '"Sarcoidosis, Pulmonary"[MeSH Terms] OR '
        '"Sarcoidosis"[MeSH Terms] OR '
        '"Pulmonary Fibrosis"[MeSH Terms] OR '
        '"Pulmonary Alveolar Proteinosis"[MeSH Terms] OR '
        '"Pulmonary Eosinophilia"[MeSH Terms] OR '
        # MeSH connective tissue diseases with pulmonary involvement
        '"Scleroderma, Systemic"[MeSH Terms] OR '
        '"Arthritis, Rheumatoid"[MeSH Terms] OR '
        '"Sjogren\'s Syndrome"[MeSH Terms] OR '
        '"Dermatomyositis"[MeSH Terms] OR '
        '"Polymyositis"[MeSH Terms] OR '
        '"Mixed Connective Tissue Disease"[MeSH Terms] OR '
        # 2025 ATS/ERS patterns — interstitial
        '"UIP"[Title/Abstract] OR '
        '"usual interstitial pneumonia"[Title/Abstract] OR '
        '"NSIP"[Title/Abstract] OR '
        '"nonspecific interstitial pneumonia"[Title/Abstract] OR '
        '"COP"[Title/Abstract] OR '
        '"cryptogenic organizing pneumonia"[Title/Abstract] OR '
        '"organizing pneumonia"[Title/Abstract] OR '
        '"AIP"[Title/Abstract] OR '
        '"acute interstitial pneumonia"[Title/Abstract] OR '
        '"LIP"[Title/Abstract] OR '
        '"lymphoid interstitial pneumonia"[Title/Abstract] OR '
        '"PPFE"[Title/Abstract] OR '
        '"pleuroparenchymal fibroelastosis"[Title/Abstract] OR '
        '"DIP"[Title/Abstract] OR '
        '"desquamative interstitial pneumonia"[Title/Abstract] OR '
        '"RB-ILD"[Title/Abstract] OR '
        '"respiratory bronchiolitis"[Title/Abstract] OR '
        # NEW in 2025: bronchiolocentric pattern
        '"BIP"[Title/Abstract] OR '
        '"bronchiolocentric interstitial pneumonia"[Title/Abstract] OR '
        # 2025 ATS/ERS — alveolar filling disorders
        '"PAP"[Title/Abstract] OR '
        '"pulmonary alveolar proteinosis"[Title/Abstract] OR '
        '"AEP"[Title/Abstract] OR '
        '"acute eosinophilic pneumonia"[Title/Abstract] OR '
        '"CEP"[Title/Abstract] OR '
        '"chronic eosinophilic pneumonia"[Title/Abstract] OR '
        '"alveolar macrophage pneumonia"[Title/Abstract] OR '
        # General ILD terms
        '"progressive pulmonary fibrosis"[Title/Abstract] OR '
        '"PPF"[Title/Abstract] OR '
        '"fibrosing ILD"[Title/Abstract] OR '
        '"fibrotic ILD"[Title/Abstract] OR '
        '"unclassifiable ILD"[Title/Abstract] OR '
        '"interstitial lung disease"[Title/Abstract] OR '
        # Clinical events specific to ILD
        '"acute exacerbation IPF"[Title/Abstract] OR '
        '"AE-IPF"[Title/Abstract] OR '
        '"acute exacerbation interstitial"[Title/Abstract] OR '
        '"lung transplant interstitial"[Title/Abstract] OR '
        # Therapy — contextualize antifibrotic with lung context to avoid
        # cardiac/hepatic/renal fibrosis false positives
        '"nintedanib"[Title/Abstract] OR '
        '"pirfenidone"[Title/Abstract] OR '
        '"BI 1015550"[Title/Abstract] OR '
        '"nerandomilast"[Title/Abstract] OR '
        '('
        '"antifibrotic"[Title/Abstract] AND '
        '('
        '"lung"[Title/Abstract] OR "pulmonary"[Title/Abstract] OR '
        '"interstitial"[Title/Abstract] OR "IPF"[Title/Abstract] OR '
        '"ILD"[Title/Abstract] OR "PPF"[Title/Abstract]'
        ')'
        ')'
        ') NOT ('
        'Editorial[Publication Type] OR Letter[Publication Type] OR '
        'Comment[Publication Type] OR Errata[Publication Type] OR '
        'Retraction of Publication[Publication Type]'
        ')'
    ),

    # ─────────────────────────────────────────────────────────────
    # Focus 2: Imaging toracico
    # Scope ALLARGATO oltre ILD: include anche lung cancer screening,
    # gestione del nodulo polmonare, e tutto il chest imaging clinico.
    # Mantenuto nome interno "ild_imaging" per compatibilità test.
    # ─────────────────────────────────────────────────────────────
    "ild_imaging": (
        '('
        # ── ARM 1: ILD imaging classico ────────────────────────
        '('
        '('
        # Imaging modality
        '"Tomography, X-Ray Computed"[MeSH Terms] OR '
        '"Magnetic Resonance Imaging"[MeSH Terms] OR '
        '"Positron Emission Tomography Computed Tomography"[MeSH Terms] OR '
        '"Lung/diagnostic imaging"[MeSH Terms] OR '
        '"HRCT"[Title/Abstract] OR '
        '"high-resolution computed tomography"[Title/Abstract] OR '
        '"quantitative CT"[Title/Abstract] OR '
        '"qCT"[Title/Abstract] OR '
        '"radiomics"[Title/Abstract] OR '
        '"texture analysis"[Title/Abstract] OR '
        '"CALIPER"[Title/Abstract] OR '
        '"FDG-PET"[Title/Abstract] OR '
        '"lung ultrasound"[Title/Abstract] OR '
        '"pattern recognition"[Title/Abstract]'
        ') AND ('
        # ILD context — aligned with 2025 ATS/ERS
        '"Lung Diseases, Interstitial"[MeSH Terms] OR '
        '"Idiopathic Pulmonary Fibrosis"[MeSH Terms] OR '
        '"Pulmonary Fibrosis"[MeSH Terms] OR '
        '"Hypersensitivity Pneumonitis"[MeSH Terms] OR '
        '"Sarcoidosis, Pulmonary"[MeSH Terms] OR '
        '"interstitial lung"[Title/Abstract] OR '
        '"ILD"[Title/Abstract] OR '
        '"IPF"[Title/Abstract] OR '
        '"UIP pattern"[Title/Abstract] OR '
        '"NSIP"[Title/Abstract] OR '
        '"COP"[Title/Abstract] OR '
        '"BIP"[Title/Abstract] OR '
        '"PPFE"[Title/Abstract] OR '
        '"fibrosing"[Title/Abstract] OR '
        '"pulmonary fibrosis"[Title/Abstract] OR '
        '"sarcoidosis"[Title/Abstract] OR '
        '"alveolar proteinosis"[Title/Abstract]'
        ')'
        ')'
        ' OR '
        # ── ARM 2: Lung cancer screening + nodule management ──
        '('
        '('
        '"Mass Screening"[MeSH Terms] OR '
        '"Early Detection of Cancer"[MeSH Terms] OR '
        '"low-dose CT"[Title/Abstract] OR '
        '"LDCT"[Title/Abstract] OR '
        '"lung cancer screening"[Title/Abstract] OR '
        '"Lung-RADS"[Title/Abstract] OR '
        '"LungRADS"[Title/Abstract] OR '
        '"Fleischner Society"[Title/Abstract] OR '
        '"pulmonary nodule"[Title/Abstract] OR '
        '"lung nodule"[Title/Abstract] OR '
        '"solitary pulmonary nodule"[Title/Abstract] OR '
        '"subsolid nodule"[Title/Abstract] OR '
        '"ground glass nodule"[Title/Abstract] OR '
        '"GGN"[Title/Abstract] OR '
        '"NLST"[Title/Abstract] OR '
        '"NELSON trial"[Title/Abstract] OR '
        '"I-ELCAP"[Title/Abstract]'
        ') AND ('
        '"Lung Neoplasms"[MeSH Terms] OR '
        '"Tomography, X-Ray Computed"[MeSH Terms] OR '
        '"lung cancer"[Title/Abstract] OR '
        '"pulmonary"[Title/Abstract] OR '
        '"thoracic"[Title/Abstract] OR '
        '"lung"[Title/Abstract]'
        ')'
        ')'
        ' OR '
        # ── ARM 3: AI/ML in chest imaging, ILD or screening context ──
        '('
        '('
        '"deep learning"[Title/Abstract] OR '
        '"machine learning"[Title/Abstract] OR '
        '"artificial intelligence"[Title/Abstract] OR '
        '"convolutional neural network"[Title/Abstract]'
        ') AND ('
        '"HRCT"[Title/Abstract] OR "CT"[Title/Abstract] OR '
        '"chest imaging"[Title/Abstract] OR "thoracic imaging"[Title/Abstract]'
        ') AND ('
        '"interstitial lung"[Title/Abstract] OR '
        '"ILD"[Title/Abstract] OR '
        '"IPF"[Title/Abstract] OR '
        '"pulmonary fibrosis"[Title/Abstract] OR '
        '"fibrosing"[Title/Abstract] OR '
        '"sarcoidosis"[Title/Abstract] OR '
        '"lung nodule"[Title/Abstract] OR '
        '"pulmonary nodule"[Title/Abstract] OR '
        '"lung cancer"[Title/Abstract] OR '
        '"lung cancer screening"[Title/Abstract]'
        ')'
        ')'
        ') NOT ('
        'Editorial[Publication Type] OR Letter[Publication Type] OR '
        'Comment[Publication Type] OR Errata[Publication Type]'
        ')'
    ),

    # ─────────────────────────────────────────────────────────────
    # Focus 3: Cardio imaging
    # Coverage: CMR, cardiac CT, photon-counting, strain,
    # stress imaging, T1/T2/T2* mapping, LGE.
    # Clinica: cardiomiopatie, amiloidosi, sarcoidosi cardiaca,
    # ischemia, pericardio, ARVD/ARVC.
    # ─────────────────────────────────────────────────────────────
    "cardio_imaging": (
        '('
        '('
        # Imaging modality
        '"Magnetic Resonance Imaging, Cine"[MeSH Terms] OR '
        '"Cardiac Imaging Techniques"[MeSH Terms] OR '
        '"Computed Tomography Angiography"[MeSH Terms] OR '
        '"Multidetector Computed Tomography"[MeSH Terms] OR '
        '"Heart/diagnostic imaging"[MeSH Terms] OR '
        '"cardiac MRI"[Title/Abstract] OR '
        '"cardiac MR"[Title/Abstract] OR '
        '"CMR"[Title/Abstract] OR '
        '"cardiac CT"[Title/Abstract] OR '
        '"coronary CT angiography"[Title/Abstract] OR '
        '"CCTA"[Title/Abstract] OR '
        '"FFR-CT"[Title/Abstract] OR '
        '"photon counting CT"[Title/Abstract] OR '
        '"T1 mapping"[Title/Abstract] OR '
        '"T2 mapping"[Title/Abstract] OR '
        '"T2* mapping"[Title/Abstract] OR '
        '"LGE"[Title/Abstract] OR '
        '"late gadolinium enhancement"[Title/Abstract] OR '
        '"strain imaging"[Title/Abstract] OR '
        '"feature tracking"[Title/Abstract] OR '
        '"stress CMR"[Title/Abstract] OR '
        '"stress CT"[Title/Abstract] OR '
        '"perfusion CMR"[Title/Abstract]'
        ') AND ('
        # Clinical context
        '"Cardiomyopathies"[MeSH Terms] OR '
        '"Cardiomyopathy, Hypertrophic"[MeSH Terms] OR '
        '"Cardiomyopathy, Dilated"[MeSH Terms] OR '
        '"Arrhythmogenic Right Ventricular Dysplasia"[MeSH Terms] OR '
        '"Myocardial Infarction"[MeSH Terms] OR '
        '"Coronary Artery Disease"[MeSH Terms] OR '
        '"Myocardial Ischemia"[MeSH Terms] OR '
        '"Cardiac Amyloidosis"[MeSH Terms] OR '
        '"Heart Failure"[MeSH Terms] OR '
        '"Myocarditis"[MeSH Terms] OR '
        '"Pericarditis"[MeSH Terms] OR '
        '"Pericardial Effusion"[MeSH Terms] OR '
        '"Heart Diseases"[MeSH Major Topic] OR '
        '"ischemic heart disease"[Title/Abstract] OR '
        '"amyloid cardiomyopathy"[Title/Abstract] OR '
        '"hypertrophic cardiomyopathy"[Title/Abstract] OR '
        '"dilated cardiomyopathy"[Title/Abstract] OR '
        '"arrhythmogenic"[Title/Abstract] OR '
        '"ARVD"[Title/Abstract] OR '
        '"ARVC"[Title/Abstract] OR '
        '"cardiac sarcoidosis"[Title/Abstract] OR '
        '"pericardial disease"[Title/Abstract] OR '
        '"iron overload"[Title/Abstract]'
        ')'
        ') NOT ('
        '"echocardiography"[Title] OR '
        'Editorial[Publication Type] OR Letter[Publication Type] OR '
        'Comment[Publication Type] OR Errata[Publication Type]'
        ')'
    ),
}

# arXiv configuration: categories to scan + keywords to filter.
# arXiv search syntax: cat:eess.IV AND (all:lung OR all:pulmonary)
ARXIV_CATEGORIES = ["eess.IV", "q-bio.QM", "cs.CV"]  # imaging + biomed + computer vision

ARXIV_KEYWORDS = {
    "ild_clinica": [],  # arXiv è metodologico, salta clinica pura
    "ild_imaging": [
        # ILD imaging
        "interstitial lung",
        "pulmonary fibrosis",
        "ILD",
        "HRCT",
        "IPF",
        "lung fibrosis",
        "UIP pattern",
        "sarcoidosis",
        # Lung cancer screening / nodule detection (new scope)
        "lung cancer screening",
        "lung nodule detection",
        "pulmonary nodule",
        "Lung-RADS",
        "lung cancer CT",
        # Generic chest imaging methods
        "chest CT segmentation",
        "thoracic imaging deep learning",
    ],
    "cardio_imaging": [
        "cardiac MRI",
        "cardiac CT",
        "CMR",
        "coronary CT",
        "myocardial",
        "cardiomyopathy",
        "late gadolinium",
        "T1 mapping",
        "strain imaging",
        "feature tracking",
    ],
}

# medRxiv keywords for client-side filtering (the API doesn't support queries).
# We download all recent papers and keep only those matching any keyword.
MEDRXIV_KEYWORDS = [
    # ILD clinica — 2025 ATS/ERS classification patterns
    "interstitial lung",
    "pulmonary fibrosis",
    "ILD",
    "IPF",
    "UIP",
    "NSIP",
    "PPF",
    "COP",
    "organizing pneumonia",
    "AIP",
    "LIP",
    "PPFE",
    "pleuroparenchymal fibroelastosis",
    "DIP",
    "RB-ILD",
    "BIP",
    "bronchiolocentric",
    "PAP",
    "alveolar proteinosis",
    "eosinophilic pneumonia",
    "AEP",
    "CEP",
    # ILD secondary
    "sarcoidosis",
    "hypersensitivity pneumonitis",
    "scleroderma lung",
    "rheumatoid lung",
    "systemic sclerosis",
    # ILD therapy
    "nintedanib",
    "pirfenidone",
    "antifibrotic",
    # ILD imaging extras
    "HRCT",
    "radiomics",
    "CALIPER",
    "FDG-PET sarcoid",
    "lung ultrasound",
    # Lung cancer screening (NEW scope)
    "lung cancer screening",
    "low-dose CT",
    "LDCT",
    "Lung-RADS",
    "lung nodule",
    "pulmonary nodule",
    "Fleischner",
    "subsolid nodule",
    # Cardio imaging
    "cardiac MRI",
    "cardiac MR",
    "cardiac CT",
    "CMR",
    "coronary CT angiography",
    "CCTA",
    "FFR-CT",
    "photon counting CT",
    "late gadolinium enhancement",
    "T1 mapping",
    "T2 mapping",
    "strain imaging",
    "feature tracking",
    "cardiomyopathy",
    "cardiac amyloid",
    "myocarditis",
    "cardiac sarcoidosis",
    "arrhythmogenic",
    "pericardial",
]

# RSS feeds: high-quality radiology and pulmonology journals.
# Format: list of (url, source_label, focus_hint)
# NOTE: After first diagnostic, RSNA and JACC feed URLs returned 404 and
# Springer's old URL returned 400. Removed those. Remaining feeds are
# "best effort" — if some still fail, PubMed queries cover the same content
# with 1-3 day latency, so it's not critical.
RSS_FEEDS = [
    ("https://www.atsjournals.org/feed/ajrccm/recent",
     "AJRCCM", "ild_clinica"),
    ("https://erj.ersjournals.com/rss/current.xml",
     "European Respiratory Journal", "ild_clinica"),
    ("https://journal.chestnet.org/action/showFeed?type=etoc&feed=rss&jc=chest",
     "Chest", "ild_clinica"),
    ("https://insightsimaging.springeropen.com/articles/most-recent/rss.xml",
     "Insights into Imaging", "mixed"),
]

# Guideline source pages (HTML pages to scrape lightly for recent updates).
# We don't deep-scrape PDFs in V1 — just detect "new on the page" via title/date.
GUIDELINE_SOURCES = [
    ("https://www.thoracic.org/statements/", "ATS Statements"),
    ("https://www.ersnet.org/guidelines/", "ERS Guidelines"),
    ("https://radiologyassistant.nl/", "Radiology Assistant (educational, ESR-affiliated)"),
    ("https://www.fleischner.org/", "Fleischner Society"),
    ("https://www.sirm.org/category/societa-italiana/", "SIRM"),
]

# Industry RSS / news endpoints (if no RSS, falls back to HTML check)
INDUSTRY_SOURCES = [
    ("https://www.boehringer-ingelheim.com/feed", "Boehringer Ingelheim"),
    ("https://www.siemens-healthineers.com/press-room", "Siemens Healthineers"),
    ("https://www.gehealthcare.com/about/newsroom", "GE HealthCare"),
    ("https://www.usa.philips.com/healthcare/about/news", "Philips Healthcare"),
]


# ════════════════════════════════════════════════════════════════════
# MODULE-LEVEL STATE (populated by init_briefs)
# ════════════════════════════════════════════════════════════════════

_NCBI_API_KEY: str = ""
_NCBI_EMAIL: str = ""
_NCBI_TOOL: str = "RadioLearnBriefs"
_GH_API: Optional[str] = None
_GH_HDR: Optional[dict] = None


def init_briefs(gh_token: str,
                gh_repo: str = "dandreagabriele-sudo/radiolearn-briefs",
                ncbi_api_key: str = "",
                ncbi_email: str = "",
                ncbi_tool: str = "RadioLearnBriefs") -> None:
    """Initialize module-level config. Call once at routine bootstrap."""
    global _NCBI_API_KEY, _NCBI_EMAIL, _NCBI_TOOL, _GH_API, _GH_HDR
    _NCBI_API_KEY = ncbi_api_key or ""
    _NCBI_EMAIL = ncbi_email or ""
    _NCBI_TOOL = ncbi_tool or "RadioLearnBriefs"
    _GH_API = f"https://api.github.com/repos/{gh_repo}/contents"
    _GH_HDR = {
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ════════════════════════════════════════════════════════════════════
# HTTP UTILITY
# ════════════════════════════════════════════════════════════════════

def _http_get(url: str, headers: Optional[dict] = None,
              timeout: int = 60, max_retries: int = 2) -> bytes:
    """GET request with basic retry logic. Returns raw response bytes."""
    # Default headers: include browser-like User-Agent to avoid WAF blocks.
    # Many academic/industry sites return 403 to default Python user-agent.
    base_headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    if headers:
        base_headers.update(headers)

    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=base_headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_exc = e
            if attempt < max_retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"HTTP GET failed after retries: {url} — {last_exc}")


def _date_back(days: int) -> str:
    """Return YYYY/MM/DD format date `days` days ago (UTC)."""
    d = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    return d.strftime("%Y/%m/%d")


def _iso_back(days: int) -> str:
    """Return YYYY-MM-DD format date `days` days ago (UTC)."""
    d = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    return d.strftime("%Y-%m-%d")


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().strftime("%Y-%m-%d")


# ════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ════════════════════════════════════════════════════════════════════

@dataclass
class Paper:
    """A scientific paper from any source."""
    title: str
    authors: list = field(default_factory=list)
    journal: str = ""
    publication_date: str = ""  # YYYY-MM-DD or YYYY-MM (best available)
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""               # PMC ID if open access
    abstract: str = ""
    url: str = ""                 # Best URL to read (PMC > preprint > DOI)
    source: str = ""              # "PubMed", "arXiv", "medRxiv", "RSS:<journal>"
    focus_hint: str = ""          # "ild_clinica", "ild_imaging", "cardio_imaging" if known
    open_access: bool = False     # True if full text reachable
    fulltext: str = ""            # Populated by enrich_*() if available
    raw: dict = field(default_factory=dict)  # original metadata for debugging

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Document:
    """A non-paper document: guideline, position paper, industry release."""
    title: str
    source: str           # "ATS Statements", "Boehringer", etc.
    date_seen: str = ""   # YYYY-MM-DD (when we first observed it)
    url: str = ""
    summary: str = ""     # Short description if available
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ════════════════════════════════════════════════════════════════════
# GITHUB CONTENTS API
# ════════════════════════════════════════════════════════════════════

def gh_get(path: str):
    """Returns (content_str, sha) or (None, None) if 404."""
    url = f"{_GH_API}/{path}?ref=main"
    try:
        raw = _http_get(url, headers=_GH_HDR)
    except RuntimeError as e:
        if "404" in str(e):
            return None, None
        raise
    j = json.loads(raw)
    content = base64.b64decode(j["content"]).decode("utf-8")
    return content, j["sha"]


def gh_list(folder: str) -> list:
    """List items in folder; [] if folder absent."""
    url = f"{_GH_API}/{folder}?ref=main"
    try:
        raw = _http_get(url, headers=_GH_HDR)
    except RuntimeError as e:
        if "404" in str(e):
            return []
        raise
    return json.loads(raw)


def gh_put(path: str, content_str: str, msg: str,
           sha: Optional[str] = None) -> str:
    """Create or update a file via Contents API."""
    payload = {
        "message": msg,
        "content": base64.b64encode(content_str.encode("utf-8")).decode("ascii"),
        "branch": "main",
    }
    if sha is not None:
        payload["sha"] = sha
    req = urllib.request.Request(
        f"{_GH_API}/{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={**_GH_HDR, "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["content"]["sha"]


def gh_delete(path: str, sha: str, msg: str) -> None:
    """Delete a file via Contents API."""
    payload = {"message": msg, "sha": sha, "branch": "main"}
    req = urllib.request.Request(
        f"{_GH_API}/{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={**_GH_HDR, "Content-Type": "application/json"},
        method="DELETE",
    )
    urllib.request.urlopen(req, timeout=30)


# ════════════════════════════════════════════════════════════════════
# PUBMED (NCBI E-utilities)
# ════════════════════════════════════════════════════════════════════

_NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _ncbi_params() -> dict:
    """Common URL params for NCBI requests (API key, tool, email)."""
    p = {"tool": _NCBI_TOOL}
    if _NCBI_API_KEY:
        p["api_key"] = _NCBI_API_KEY
    if _NCBI_EMAIL:
        p["email"] = _NCBI_EMAIL
    return p


def fetch_pubmed(focus_name: str, days_back: int = 10,
                 max_results: int = 200) -> list:
    """Fetch recent PubMed papers for a given focus.

    Uses esearch to get PMIDs, then efetch to get abstracts in XML.
    """
    if focus_name not in PUBMED_QUERIES:
        raise ValueError(f"Unknown focus: {focus_name}")

    base_query = PUBMED_QUERIES[focus_name]
    date_filter = f' AND ("{_date_back(days_back)}"[EDAT] : "{_date_back(0)}"[EDAT])'
    full_query = base_query + date_filter

    # Step 1: esearch — get PMIDs
    esearch_params = {
        **_ncbi_params(),
        "db": "pubmed",
        "term": full_query,
        "retmax": str(max_results),
        "retmode": "json",
        "sort": "date",
    }
    esearch_url = f"{_NCBI_BASE}/esearch.fcgi?{urllib.parse.urlencode(esearch_params)}"
    esearch_raw = _http_get(esearch_url)
    esearch_json = json.loads(esearch_raw)
    pmids = esearch_json.get("esearchresult", {}).get("idlist", [])

    if not pmids:
        return []

    # Rate-limit politeness (3/sec without key, 10/sec with key)
    time.sleep(0.15 if _NCBI_API_KEY else 0.4)

    # Step 2: efetch — get abstracts in XML
    efetch_params = {
        **_ncbi_params(),
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    efetch_url = f"{_NCBI_BASE}/efetch.fcgi?{urllib.parse.urlencode(efetch_params)}"
    efetch_raw = _http_get(efetch_url)

    # Step 3: parse XML
    papers = _parse_pubmed_xml(efetch_raw, focus_name)
    return papers


def _parse_pubmed_xml(xml_bytes: bytes, focus_hint: str) -> list:
    """Parse PubMed XML response into Paper objects."""
    papers = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print(f"  ⚠ PubMed XML parse error: {e}")
        return papers

    for article in root.findall(".//PubmedArticle"):
        try:
            paper = _pubmed_article_to_paper(article, focus_hint)
            if paper:
                papers.append(paper)
        except Exception as e:
            print(f"  ⚠ Error parsing article: {e}")
            continue

    return papers


def _pubmed_article_to_paper(article: ET.Element, focus_hint: str) -> Optional[Paper]:
    """Convert a PubmedArticle XML element to Paper."""
    # PMID
    pmid_el = article.find(".//PMID")
    pmid = pmid_el.text if pmid_el is not None else ""

    # Title (can contain inline formatting tags, we extract all text)
    title_el = article.find(".//ArticleTitle")
    title = _text_recursive(title_el) if title_el is not None else ""

    # Abstract (may have multiple sections)
    abstract_parts = []
    for ab in article.findall(".//Abstract/AbstractText"):
        label = ab.get("Label", "")
        text = _text_recursive(ab)
        if label:
            abstract_parts.append(f"{label}: {text}")
        else:
            abstract_parts.append(text)
    abstract = "\n".join(abstract_parts).strip()

    # Authors
    authors = []
    for author in article.findall(".//AuthorList/Author"):
        last = author.findtext("LastName", "")
        initials = author.findtext("Initials", "")
        if last:
            authors.append(f"{last} {initials}".strip())

    # Journal
    journal = article.findtext(".//Journal/Title", "") or \
              article.findtext(".//Journal/ISOAbbreviation", "")

    # Publication date — use ArticleDate (EPub) if present, else PubDate
    pub_date = ""
    article_date = article.find(".//ArticleDate")
    if article_date is not None:
        y = article_date.findtext("Year", "")
        m = article_date.findtext("Month", "").zfill(2)
        d = article_date.findtext("Day", "").zfill(2)
        if y:
            pub_date = f"{y}-{m or '01'}-{d or '01'}"
    if not pub_date:
        pub_date_el = article.find(".//Journal/JournalIssue/PubDate")
        if pub_date_el is not None:
            y = pub_date_el.findtext("Year", "")
            m_text = pub_date_el.findtext("Month", "")
            m = _month_to_num(m_text)
            if y:
                pub_date = f"{y}-{m or '01'}"

    # DOI
    doi = ""
    for aid in article.findall(".//ArticleIdList/ArticleId"):
        if aid.get("IdType") == "doi":
            doi = aid.text or ""
            break

    # PMCID (if available, paper is open access)
    pmcid = ""
    for aid in article.findall(".//ArticleIdList/ArticleId"):
        if aid.get("IdType") == "pmc":
            pmcid = aid.text or ""
            break

    url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else (
        f"https://doi.org/{doi}" if doi else ""
    )

    return Paper(
        title=title,
        authors=authors,
        journal=journal,
        publication_date=pub_date,
        doi=doi,
        pmid=pmid,
        pmcid=pmcid,
        abstract=abstract,
        url=url,
        source="PubMed",
        focus_hint=focus_hint,
        open_access=bool(pmcid),
    )


def _text_recursive(el: Optional[ET.Element]) -> str:
    """Extract all text content from an XML element, including children."""
    if el is None:
        return ""
    return "".join(el.itertext()).strip()


def _month_to_num(month: str) -> str:
    """Convert month name (Jan, Feb, ...) or number to 2-digit number."""
    if not month:
        return ""
    if month.isdigit():
        return month.zfill(2)
    months = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
              "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
              "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}
    return months.get(month[:3], "")


# ════════════════════════════════════════════════════════════════════
# ARXIV
# ════════════════════════════════════════════════════════════════════

_ARXIV_BASE = "http://export.arxiv.org/api/query"


def fetch_arxiv(focus_name: str, days_back: int = 10,
                max_results: int = 100) -> list:
    """Fetch recent arXiv preprints matching a focus.

    Builds a query: (cat:X OR cat:Y) AND (all:keyword1 OR all:keyword2...)
    Filters client-side by submitted date.
    """
    keywords = ARXIV_KEYWORDS.get(focus_name, [])
    if not keywords:
        return []  # no keywords for this focus

    cats = " OR ".join(f"cat:{c}" for c in ARXIV_CATEGORIES)
    kws = " OR ".join(f'all:"{kw}"' for kw in keywords)
    search_query = f"({cats}) AND ({kws})"

    params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
        "start": "0",
    }
    url = f"{_ARXIV_BASE}?{urllib.parse.urlencode(params)}"

    raw = _http_get(url)
    papers = _parse_arxiv_atom(raw, focus_name, days_back)
    return papers


def _parse_arxiv_atom(atom_bytes: bytes, focus_hint: str,
                      days_back: int) -> list:
    """Parse arXiv Atom XML into Paper objects, filtered by date."""
    papers = []
    ns = {
        "a": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        root = ET.fromstring(atom_bytes)
    except ET.ParseError as e:
        print(f"  ⚠ arXiv XML parse error: {e}")
        return papers

    for entry in root.findall("a:entry", ns):
        try:
            published_str = entry.findtext("a:published", "", ns)
            if not published_str:
                continue
            published = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            if published < cutoff:
                continue  # too old

            title = (entry.findtext("a:title", "", ns) or "").strip()
            title = re.sub(r"\s+", " ", title)
            summary = (entry.findtext("a:summary", "", ns) or "").strip()
            summary = re.sub(r"\s+", " ", summary)

            authors = []
            for a in entry.findall("a:author", ns):
                name = a.findtext("a:name", "", ns)
                if name:
                    authors.append(name)

            arxiv_id_url = (entry.findtext("a:id", "", ns) or "").strip()
            arxiv_id = arxiv_id_url.split("/abs/")[-1] if "/abs/" in arxiv_id_url else ""

            doi = entry.findtext("arxiv:doi", "", ns)
            pdf_url = ""
            for link in entry.findall("a:link", ns):
                if link.get("type") == "application/pdf":
                    pdf_url = link.get("href") or ""
                    break

            papers.append(Paper(
                title=title,
                authors=authors,
                journal="arXiv preprint",
                publication_date=published_str[:10],
                doi=doi or "",
                pmid="",
                pmcid="",
                abstract=summary,
                url=arxiv_id_url,
                source="arXiv",
                focus_hint=focus_hint,
                open_access=True,
                raw={"arxiv_id": arxiv_id, "pdf_url": pdf_url},
            ))
        except Exception as e:
            print(f"  ⚠ arXiv parse entry error: {e}")
            continue

    return papers


# ════════════════════════════════════════════════════════════════════
# MEDRXIV
# ════════════════════════════════════════════════════════════════════

_MEDRXIV_BASE = "https://api.biorxiv.org/details/medrxiv"


def fetch_medrxiv(days_back: int = 10, max_papers: int = 5000) -> list:
    """Fetch recent medRxiv preprints. API doesn't support search queries,
    so we fetch all in the date range and filter client-side by keywords.
    """
    end = _iso_back(0)
    start = _iso_back(days_back)
    all_papers = []
    cursor = 0

    while len(all_papers) < max_papers:
        url = f"{_MEDRXIV_BASE}/{start}/{end}/{cursor}/json"
        try:
            raw = _http_get(url, timeout=60)
        except RuntimeError as e:
            print(f"  ⚠ medRxiv fetch error at cursor={cursor}: {e}")
            break

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  ⚠ medRxiv JSON decode error at cursor={cursor}")
            break

        collection = data.get("collection", [])
        if not collection:
            break

        all_papers.extend(collection)
        cursor += len(collection)

        # Stop if we got fewer than 100 (end of results)
        if len(collection) < 100:
            break

        time.sleep(0.3)  # be nice to medRxiv server

    # Client-side keyword filter using word boundaries to avoid false positives.
    # Without \b boundaries, "ILD" matches inside "child", "PAP" inside "Pappilon",
    # etc. — which is exactly the bug we saw in the first diagnostic run.
    filtered = []
    keyword_patterns = [
        re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
        for kw in MEDRXIV_KEYWORDS
    ]

    for item in all_papers:
        title = item.get("title", "") or ""
        abstract = item.get("abstract", "") or ""
        searchable = title + " " + abstract
        if any(p.search(searchable) for p in keyword_patterns):
            filtered.append(_medrxiv_item_to_paper(item))

    return filtered


def _medrxiv_item_to_paper(item: dict) -> Paper:
    """Convert a medRxiv API item to Paper."""
    doi = item.get("doi", "")
    title = item.get("title", "").strip()
    abstract = item.get("abstract", "").strip()
    authors_str = item.get("authors", "")
    authors = [a.strip() for a in authors_str.split(";") if a.strip()] if authors_str else []
    pub_date = item.get("date", "")
    pdf_url = f"https://www.medrxiv.org/content/{doi}v{item.get('version', 1)}.full.pdf"
    abs_url = f"https://www.medrxiv.org/content/{doi}v{item.get('version', 1)}"

    # Naive focus_hint inference based on keywords in title
    title_lower = title.lower()
    focus = ""
    if any(k in title_lower for k in ["cardiac", "coronary", "ffr-ct", "cmr", "myocardial"]):
        focus = "cardio_imaging"
    elif any(k in title_lower for k in ["hrct", "imaging", "radiomics", "deep learning", "ct"]):
        focus = "ild_imaging"
    elif any(k in title_lower for k in ["interstitial", "ipf", "fibrosis", "ild", "ppf"]):
        focus = "ild_clinica"

    return Paper(
        title=title,
        authors=authors,
        journal="medRxiv preprint",
        publication_date=pub_date,
        doi=doi,
        pmid="",
        pmcid="",
        abstract=abstract,
        url=abs_url,
        source="medRxiv",
        focus_hint=focus,
        open_access=True,
        raw={"pdf_url": pdf_url, "version": item.get("version")},
    )


# ════════════════════════════════════════════════════════════════════
# RSS FEEDS
# ════════════════════════════════════════════════════════════════════

def fetch_rss(feed_url: str, source_label: str, focus_hint: str = "",
              days_back: int = 10, max_items: int = 50) -> list:
    """Parse an RSS feed and return Paper objects for items within date window."""
    try:
        raw = _http_get(feed_url, timeout=30)
    except RuntimeError as e:
        print(f"  ⚠ RSS fetch error for {source_label}: {e}")
        return []

    papers = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"  ⚠ RSS parse error for {source_label}: {e}")
        return papers

    # RSS 2.0 (channel/item) or Atom (entry)
    items = root.findall(".//item")
    if not items:
        # Try Atom
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")

    for item in items[:max_items]:
        try:
            paper = _rss_item_to_paper(item, source_label, focus_hint, cutoff)
            if paper:
                papers.append(paper)
        except Exception as e:
            print(f"  ⚠ RSS item parse error in {source_label}: {e}")
            continue

    return papers


def _rss_item_to_paper(item: ET.Element, source_label: str,
                      focus_hint: str, cutoff: datetime) -> Optional[Paper]:
    """Convert an RSS/Atom item to a Paper."""
    ns_atom = "{http://www.w3.org/2005/Atom}"

    # Title
    title = (item.findtext("title") or item.findtext(f"{ns_atom}title") or "").strip()
    title = unescape(re.sub(r"\s+", " ", title))

    # Date
    date_str = (item.findtext("pubDate") or item.findtext(f"{ns_atom}published") or
                item.findtext(f"{ns_atom}updated") or item.findtext("{http://purl.org/dc/elements/1.1/}date") or "")

    pub_date = _parse_rss_date(date_str)
    if pub_date and pub_date < cutoff:
        return None

    # Description / summary
    description = (item.findtext("description") or item.findtext(f"{ns_atom}summary") or
                   item.findtext(f"{ns_atom}content") or "")
    description = unescape(re.sub(r"<[^>]+>", " ", description))  # strip HTML
    description = re.sub(r"\s+", " ", description).strip()

    # Link
    link = ""
    link_el = item.find("link")
    if link_el is not None and link_el.text:
        link = link_el.text.strip()
    else:
        atom_link = item.find(f"{ns_atom}link")
        if atom_link is not None:
            link = atom_link.get("href", "")

    # Authors (often missing in RSS)
    authors = []
    for a in item.findall(f"{ns_atom}author/{ns_atom}name"):
        if a.text:
            authors.append(a.text.strip())
    creator = item.findtext("{http://purl.org/dc/elements/1.1/}creator")
    if creator and not authors:
        authors = [a.strip() for a in creator.split(",")]

    # DOI: try to extract from link
    doi = ""
    m = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", link, re.IGNORECASE)
    if m:
        doi = m.group(0)

    return Paper(
        title=title,
        authors=authors,
        journal=source_label,
        publication_date=pub_date.strftime("%Y-%m-%d") if pub_date else "",
        doi=doi,
        pmid="",
        pmcid="",
        abstract=description[:1500],  # cap RSS descriptions
        url=link,
        source=f"RSS:{source_label}",
        focus_hint=focus_hint if focus_hint != "mixed" else "",
        open_access=False,  # unknown by default
    )


def _parse_rss_date(s: str) -> Optional[datetime]:
    """Parse RSS/Atom date string in various formats."""
    if not s:
        return None
    s = s.strip()
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",       # RSS 2.0 RFC 822
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",            # Atom ISO 8601
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ]
    # Try ISO format with fromisoformat first (handles edge cases)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ════════════════════════════════════════════════════════════════════
# PMC FULL TEXT
# ════════════════════════════════════════════════════════════════════

def enrich_pmc_fulltext(paper: Paper) -> bool:
    """Fetch full text from PMC if PMCID is available. Mutates paper.fulltext."""
    if not paper.pmcid:
        return False

    pmcid = paper.pmcid.replace("PMC", "")  # accept "PMC1234" or "1234"
    params = {
        **_ncbi_params(),
        "db": "pmc",
        "id": pmcid,
        "retmode": "xml",
    }
    url = f"{_NCBI_BASE}/efetch.fcgi?{urllib.parse.urlencode(params)}"
    try:
        raw = _http_get(url, timeout=60)
    except RuntimeError as e:
        print(f"  ⚠ PMC fetch error for {paper.pmcid}: {e}")
        return False

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False

    # Extract body text (PMC XML structure)
    body_text = []
    for body in root.findall(".//body"):
        body_text.append(_text_recursive(body))

    if body_text:
        paper.fulltext = "\n\n".join(body_text)[:50000]  # cap at 50KB
        return True
    return False


# ════════════════════════════════════════════════════════════════════
# PREPRINT PDF TEXT (defensive — pypdf might not be installed)
# ════════════════════════════════════════════════════════════════════

def enrich_preprint_fulltext(paper: Paper) -> bool:
    """Download preprint PDF and extract text. Requires pypdf.
    Gracefully degrades if pypdf is not installed.
    """
    pdf_url = paper.raw.get("pdf_url") if paper.raw else ""
    if not pdf_url:
        # For arXiv we can construct it
        if paper.source == "arXiv" and "arxiv_id" in (paper.raw or {}):
            pdf_url = f"https://arxiv.org/pdf/{paper.raw['arxiv_id']}.pdf"
        else:
            return False

    try:
        import pypdf
    except ImportError:
        try:
            # Try to install on-the-fly
            import subprocess
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "pypdf"],
                           check=True, timeout=60)
            import pypdf
        except Exception as e:
            print(f"  ⚠ pypdf not available and could not install: {e}")
            return False

    try:
        pdf_bytes = _http_get(pdf_url, timeout=120)
    except RuntimeError as e:
        print(f"  ⚠ Preprint PDF download error: {e}")
        return False

    try:
        import io
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text_parts = []
        for page in reader.pages[:30]:  # cap at first 30 pages
            try:
                text_parts.append(page.extract_text() or "")
            except Exception:
                continue
        paper.fulltext = ("\n".join(text_parts))[:50000]
        return bool(paper.fulltext)
    except Exception as e:
        print(f"  ⚠ PDF parse error: {e}")
        return False


# ════════════════════════════════════════════════════════════════════
# GUIDELINE PAGES (lightweight HTML scrape)
# ════════════════════════════════════════════════════════════════════

def fetch_guideline_pages(days_back: int = 14) -> list:
    """Scan guideline sources for recently announced documents.

    V1 strategy: download landing page, look for items with recognizable
    date patterns near the top, return as Documents. This is intentionally
    lightweight — not exhaustive.
    """
    docs = []
    for url, label in GUIDELINE_SOURCES:
        try:
            html = _http_get(url, timeout=30).decode("utf-8", errors="replace")
        except RuntimeError as e:
            print(f"  ⚠ Guideline fetch error for {label}: {e}")
            continue

        # Very simple heuristic: find <a> tags with text + nearby date
        # This is a placeholder — will be refined after seeing real outputs
        items = _scrape_dated_items(html, url, label, days_back)
        docs.extend(items)
        time.sleep(0.5)

    return docs


def _scrape_dated_items(html: str, base_url: str, label: str,
                       days_back: int) -> list:
    """Naive scraper: find links accompanied by recognizable dates."""
    docs = []
    # Find <a href="..."> pairs with text
    link_re = re.compile(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>([^<]{10,200})</a>',
        re.IGNORECASE
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).date()

    found_links = link_re.findall(html)[:50]  # cap to avoid spam
    for href, text in found_links:
        text_clean = re.sub(r"\s+", " ", unescape(text)).strip()
        # Heuristic: must contain recognizable "document" words
        if not re.search(r"guideline|statement|consensus|position|recommendation|report",
                         text_clean, re.IGNORECASE):
            continue

        # Resolve relative URL
        if href.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith("http"):
            continue

        docs.append(Document(
            title=text_clean,
            source=label,
            date_seen=_today_iso(),
            url=href,
            summary="",
        ))

    return docs


# ════════════════════════════════════════════════════════════════════
# INDUSTRY RSS / NEWS
# ════════════════════════════════════════════════════════════════════

def fetch_industry_rss(days_back: int = 14) -> list:
    """Fetch industry press releases via RSS where available.
    Returns Documents.
    """
    docs = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    for url, label in INDUSTRY_SOURCES:
        try:
            raw = _http_get(url, timeout=30)
        except RuntimeError as e:
            print(f"  ⚠ Industry fetch error for {label}: {e}")
            continue

        # Try to parse as RSS first
        try:
            root = ET.fromstring(raw)
            items = root.findall(".//item")
            if items:
                for item in items[:20]:
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    desc = (item.findtext("description") or "").strip()
                    desc = unescape(re.sub(r"<[^>]+>", " ", desc))
                    date_str = (item.findtext("pubDate") or "").strip()
                    pub_dt = _parse_rss_date(date_str)
                    if pub_dt and pub_dt < cutoff:
                        continue
                    docs.append(Document(
                        title=title,
                        source=label,
                        date_seen=pub_dt.strftime("%Y-%m-%d") if pub_dt else _today_iso(),
                        url=link,
                        summary=desc[:500],
                    ))
                continue  # got items from RSS
        except ET.ParseError:
            pass

        # Fallback: it's HTML, do lightweight scrape (very approximate)
        try:
            html = raw.decode("utf-8", errors="replace")
            items = _scrape_dated_items(html, url, label, days_back)
            docs.extend(items[:10])
        except Exception as e:
            print(f"  ⚠ Industry HTML scrape error for {label}: {e}")
            continue

        time.sleep(0.5)

    return docs


# ════════════════════════════════════════════════════════════════════
# DEDUPLICATION & UTILITIES
# ════════════════════════════════════════════════════════════════════

def dedup_papers(papers: list) -> list:
    """Deduplicate papers by DOI (primary) or normalized title (fallback)."""
    seen_doi = set()
    seen_title = set()
    out = []
    for p in papers:
        key_doi = (p.doi or "").lower().strip()
        key_title = re.sub(r"[^a-z0-9]+", "", (p.title or "").lower())[:80]
        if key_doi and key_doi in seen_doi:
            continue
        if not key_doi and key_title in seen_title:
            continue
        if key_doi:
            seen_doi.add(key_doi)
        seen_title.add(key_title)
        out.append(p)
    return out


def papers_to_jsonable(papers: list) -> list:
    """Convert list of Paper to JSON-serializable list of dicts."""
    return [p.to_dict() if hasattr(p, "to_dict") else p for p in papers]
