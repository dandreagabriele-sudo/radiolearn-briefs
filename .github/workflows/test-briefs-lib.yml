name: Test briefs_lib

# Manual trigger only. Runs each fetcher in isolation and logs results.
# Use this BEFORE writing the full routine to validate that all sources work.

on:
  workflow_dispatch:
    inputs:
      sources:
        description: 'Which sources to test (comma-separated: pubmed,arxiv,medrxiv,rss,guidelines,industry,all)'
        required: false
        default: 'all'
      days_back:
        description: 'Days back to fetch (default 14 = biweekly cadence)'
        required: false
        default: '14'

jobs:
  test:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@v5

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Run diagnostics
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}  # built-in, read-only is fine for tests
          NCBI_API_KEY: ${{ secrets.NCBI_API_KEY }}
          NCBI_EMAIL: ${{ secrets.NCBI_EMAIL }}
          SOURCES: ${{ inputs.sources }}
          DAYS_BACK: ${{ inputs.days_back }}
        run: python3 .github/scripts/test_briefs_lib.py
