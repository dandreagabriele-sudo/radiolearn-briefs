"""
deliver_outbox.py — Outbox consumer invoked by send-to-telegram-briefs.yml.

For each file in outbox/, send its messages to Telegram and delete the file
via the GitHub Contents API. No `git push` — uses API calls only, avoiding
race conditions with parallel commits from the routine.

Env vars: TELEGRAM_BOT_TOKEN, GH_TOKEN, GH_REPO
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import briefs_lib as lib

GH_TOKEN = os.environ["GH_TOKEN"]
GH_REPO = os.environ["GH_REPO"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

lib.init_briefs(GH_TOKEN, GH_REPO, "", "")  # NCBI keys non servono qui


def telegram_call(method: str, params: dict) -> dict:
    """Call a Telegram bot API method."""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def gh_delete_with_retry(path: str, max_retries: int = 3) -> bool:
    """
    Delete a file via GitHub Contents API with retry-on-stale-sha.

    If the SHA we got is stale because another commit raced ahead, refetch
    and retry. After max_retries, give up gracefully (the file may have been
    deleted by another runner).
    """
    for attempt in range(max_retries):
        content, sha = lib.gh_get(path)
        if not content or not sha:
            # File doesn't exist anymore — someone else cleaned it up
            print(f"  ✓ {path} already gone")
            return True
        try:
            lib.gh_delete(path, sha, f"Outbox drain: remove {path}")
            print(f"  ✓ deleted {path}")
            return True
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt < max_retries - 1:
                # Stale SHA conflict — back off and retry
                print(f"  ⟳ stale SHA on attempt {attempt + 1}, retrying...")
                time.sleep(2 ** attempt)
                continue
            print(f"  ✗ delete failed: HTTP {e.code}")
            return False
        except Exception as e:
            print(f"  ✗ delete exception: {e}")
            return False
    return False


# ─── List outbox ─────────────────────────────────────────────────
print("─" * 60)
print("Listing outbox/...")

try:
    items = lib.gh_list("outbox")
except Exception as e:
    print(f"⚠ Could not list outbox (probably empty): {e}")
    sys.exit(0)

files = [it for it in items if it.get("type") == "file" and it["name"].endswith(".json")]
print(f"Found {len(files)} message file(s) to deliver")

if not files:
    print("Nothing to do. Exiting.")
    sys.exit(0)


# ─── Process each file ────────────────────────────────────────────
delivery_errors = 0

for item in files:
    path = item["path"]
    print("─" * 60)
    print(f"Processing {path}")

    content, sha = lib.gh_get(path)
    if not content:
        print(f"  ⚠ could not read {path}, skipping")
        continue

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as e:
        print(f"  ✗ invalid JSON in {path}: {e}")
        delivery_errors += 1
        continue

    messages = payload.get("messages", [])
    print(f"  → delivering {len(messages)} message(s)")

    all_sent = True
    for msg in messages:
        method = msg.get("method", "sendMessage")
        params = msg.get("params", {})
        try:
            result = telegram_call(method, params)
            if not result.get("ok"):
                print(f"  ✗ Telegram error: {result}")
                all_sent = False
        except Exception as e:
            print(f"  ✗ delivery exception: {e}")
            all_sent = False
        time.sleep(0.5)  # polite pause

    if all_sent:
        print(f"  ✓ all messages delivered; removing {path}")
        if not gh_delete_with_retry(path):
            delivery_errors += 1
    else:
        print(f"  ⚠ keeping {path} for retry next run")
        delivery_errors += 1

print("─" * 60)
if delivery_errors:
    print(f"⚠ Completed with {delivery_errors} error(s)")
    sys.exit(1)
print("✓ Outbox processing complete.")
