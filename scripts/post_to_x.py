"""Post tweet_text.txt to X. Soft-fails so a missing-secret or API hiccup
never blocks the leaderboard update / issue close.

Triggered by .github/workflows/leaderboard.yml after update_leaderboard.py
runs successfully. Reads tweet_text.txt (written by the verifier) and posts
via the X v2 API using OAuth 1.0a User Context.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED_SECRETS = (
    "X_API_KEY",
    "X_API_SECRET",
    "X_ACCESS_TOKEN",
    "X_ACCESS_TOKEN_SECRET",
)


def soft_exit(msg: str, code: int = 0) -> None:
    """Always exit 0 — we never want to break the workflow over a tweet."""
    print(msg)
    sys.exit(code)


def main() -> None:
    tweet_file = Path("tweet_text.txt")
    if not tweet_file.exists():
        soft_exit("post_to_x: no tweet_text.txt found (verifier didn't produce one) — skipping")

    tweet = tweet_file.read_text(encoding="utf-8").strip()
    if not tweet:
        soft_exit("post_to_x: empty tweet_text.txt — skipping")

    missing = [s for s in REQUIRED_SECRETS if not os.environ.get(s)]
    if missing:
        soft_exit(f"post_to_x: missing secrets {missing} — skipping (set them in repo Settings → Secrets)")

    try:
        import tweepy
    except ImportError:
        soft_exit("post_to_x: tweepy not installed — skipping")

    try:
        client = tweepy.Client(
            consumer_key=os.environ["X_API_KEY"],
            consumer_secret=os.environ["X_API_SECRET"],
            access_token=os.environ["X_ACCESS_TOKEN"],
            access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
        )
        resp = client.create_tweet(text=tweet)
        tid = resp.data.get("id") if resp and resp.data else "?"
        print(f"post_to_x: ✅ tweeted (id={tid})")
    except Exception as e:
        # Catching broad on purpose — tweepy raises lots of subclasses, and
        # we never want a tweet failure to fail the workflow.
        soft_exit(f"post_to_x: API call failed ({type(e).__name__}: {e}) — skipping")


if __name__ == "__main__":
    main()
