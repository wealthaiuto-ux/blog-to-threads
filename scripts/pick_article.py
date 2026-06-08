"""投稿対象の記事を1本選ぶ。新着優先 + 過去ランダムMIX、24時間以内に投稿済みは除外。"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "articles_cache.json"
LOG_PATH = ROOT / "data" / "posted_log.json"

NEW_RATIO = 0.7
NEW_WINDOW = 10
DEDUPE_HOURS = 24 * 7


def load_cache() -> list[dict]:
    if not CACHE_PATH.exists():
        raise SystemExit("articles_cache.json が無い。先に crawl_blog.py を実行")
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))["items"]


def load_log() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    return json.loads(LOG_PATH.read_text(encoding="utf-8"))


def recently_posted_urls(log: list[dict], hours: int = DEDUPE_HOURS) -> set[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    urls = set()
    for entry in log:
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"])
        except (KeyError, ValueError):
            continue
        if posted_at >= cutoff:
            urls.add(entry["article_url"])
    return urls


def pick(items: list[dict], excluded: set[str]) -> dict | None:
    candidates = [a for a in items if a["url"] not in excluded]
    if not candidates:
        return None
    new_pool = candidates[:NEW_WINDOW]
    old_pool = candidates[NEW_WINDOW:]
    if old_pool and random.random() > NEW_RATIO:
        return random.choice(old_pool)
    return random.choice(new_pool or candidates)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="このURLの記事を強制選択")
    args = ap.parse_args()

    items = load_cache()
    if args.url:
        for a in items:
            if a["url"] == args.url:
                print(json.dumps(a, ensure_ascii=False))
                return 0
        raise SystemExit(f"指定URL {args.url} がキャッシュに無い")

    excluded = recently_posted_urls(load_log())
    chosen = pick(items, excluded)
    if not chosen:
        raise SystemExit("投稿可能な記事が無い（全部最近投稿済み）")
    print(json.dumps(chosen, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
