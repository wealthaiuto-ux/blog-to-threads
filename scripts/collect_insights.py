"""投稿ごとの反応データ（views/likes/replies/reposts/quotes）を回収する。

投稿から48時間経過したものだけを対象にする。伸び切る前に取ると、
投稿時刻の違いがそのまま数字の差になって、型どうしの比較ができなくなるため。

出力: data/insights.json（thread_id ごとに1件）
    [{"thread_id": "...", "article_url": "...", "posted_at": "...",
      "collected_at": "...", "age_hours": 51.2,
      "views": 123, "likes": 4, "replies": 0, "reposts": 0, "quotes": 0,
      "status": "ok"}]

status:
  ok          … 取得できた
  unavailable … 削除済みなどで取得できない（attempts が上限に達したら再試行しない）

ref: https://developers.facebook.com/docs/threads/insights
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
POSTED_LOG = ROOT / "data" / "posted_log.json"
OUT_PATH = ROOT / "data" / "insights.json"

GRAPH = "https://graph.threads.net/v1.0"
METRICS = ["views", "likes", "replies", "reposts", "quotes"]
MIN_AGE_HOURS = 48
MAX_ATTEMPTS = 3


def fetch_insights(media_id: str, token: str) -> dict[str, int]:
    qs = urlencode({"metric": ",".join(METRICS), "access_token": token})
    with urlopen(f"{GRAPH}/{media_id}/insights?{qs}", timeout=30) as resp:
        body = json.loads(resp.read())

    out: dict[str, int] = {}
    for entry in body.get("data", []):
        name = entry.get("name")
        values = entry.get("values") or []
        if name in METRICS and values and "value" in values[0]:
            out[name] = int(values[0]["value"])
    return out


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def main() -> int:
    import os

    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="取得済みのものも取り直す（数字は投稿後も伸び続けるため）")
    ap.add_argument("--token-env", default="THREADS_ACCESS_TOKEN")
    ap.add_argument("--limit", type=int, default=0, help="1回に処理する最大件数（0で無制限）")
    args = ap.parse_args()

    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"{args.token_env} が未設定")

    posted = _load(POSTED_LOG, [])
    existing = {r["thread_id"]: r for r in _load(OUT_PATH, [])}
    now = datetime.now(timezone.utc)

    targets = []
    for entry in posted:
        tid = entry.get("thread_root_id")
        if not tid:
            continue
        try:
            posted_at = datetime.fromisoformat(entry["posted_at"])
        except (KeyError, ValueError):
            continue

        age_hours = (now - posted_at).total_seconds() / 3600
        if age_hours < MIN_AGE_HOURS:
            continue

        prev = existing.get(tid)
        if prev and not args.refresh:
            continue
        if prev and prev.get("status") == "unavailable" and prev.get("attempts", 0) >= MAX_ATTEMPTS:
            continue

        targets.append((tid, entry, posted_at, age_hours))

    if args.limit:
        targets = targets[:args.limit]

    print(f"[target] 投稿{len(posted)}件中 {len(targets)}件を取得", file=sys.stderr)

    ok = fail = 0
    for tid, entry, posted_at, age_hours in targets:
        record = existing.get(tid, {})
        try:
            metrics = fetch_insights(tid, token)
        except (HTTPError, URLError) as e:
            detail = ""
            if isinstance(e, HTTPError):
                detail = e.read().decode("utf-8", errors="replace")[:160]
            attempts = record.get("attempts", 0) + 1
            existing[tid] = {
                **record,
                "thread_id": tid,
                "article_url": entry.get("article_url"),
                "posted_at": entry["posted_at"],
                "status": "unavailable",
                "error": detail or str(e),
                "attempts": attempts,
                "collected_at": now.isoformat(),
            }
            print(f"[ng] {tid} ({attempts}/{MAX_ATTEMPTS}): {detail[:80]}", file=sys.stderr)
            fail += 1
            continue

        existing[tid] = {
            "thread_id": tid,
            "article_url": entry.get("article_url"),
            "article_title": entry.get("article_title"),
            "post_type": entry.get("post_type"),
            "theme": entry.get("theme"),
            "posted_at": entry["posted_at"],
            "collected_at": now.isoformat(),
            "age_hours": round(age_hours, 1),
            **{m: metrics.get(m, 0) for m in METRICS},
            "status": "ok",
            "attempts": 0,
        }
        ok += 1

    records = sorted(existing.values(), key=lambda r: r.get("posted_at", ""))
    OUT_PATH.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[done] ok={ok} ng={fail} / 累計{len(records)}件 -> {OUT_PATH.name}", file=sys.stderr)

    usable = [r for r in records if r.get("status") == "ok"]
    if usable:
        avg = sum(r["views"] for r in usable) / len(usable)
        print(f"[stat] 取得済み{len(usable)}件の平均views: {avg:.1f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
