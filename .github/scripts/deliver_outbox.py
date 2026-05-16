"""
deliver_outbox.py — Outbox consumer invoked by send-to-telegram-briefs.yml.

For each file in outbox/, send its messages to Telegram and delete the file.
This mirrors the RadioLearn `send.py` pattern but is dedicated to the briefs repo.

Env vars: TELEGRAM_BOT_TOKEN, GH_TOKEN, GH_REPO
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

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


# ─── List outbox ─────────────────────────────────────────────────
print("─" * 60)
print("Listing outbox/...")

items = lib.gh_list("outbox")
files = [it for it in items if it.get("type") == "file" and it["name"].endswith(".json")]
print(f"Found {len(files)} message file(s) to deliver")

if not files:
    print("Nothing to do. Exiting.")
    sys.exit(0)


# ─── Process each file ────────────────────────────────────────────
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
        print(f"  ✓ all messages delivered; cleaning up {path}")
        try:
            lib.gh_delete(path, sha, f"Cleanup outbox after delivery of {payload.get('id', path)}")
        except Exception as e:
            print(f"  ⚠ delete failed (file may already be gone): {e}")
    else:
        print(f"  ⚠ keeping {path} for retry next run")

print("─" * 60)
print("Outbox processing complete.")
