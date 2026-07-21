"""投稿ジョブ：Notion から status=approved の最古ページを1件取り、Threadsに投稿。

approvedが0件のときは何もせず終了（在庫切れスキップ）。
投稿直前に「直近7日で同じ記事を投稿していないか」を最終チェックする。
生成時にも重複除外はあるが、手動生成されたドラフトはその網を通らないため、
ここが最後の防衛線になる。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notion_draft  # type: ignore
import post_threads  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "posted_log.json"

# 同じ記事を再投稿してよいまでの間隔
DEDUPE_DAYS = 7
# 重複で見送ったとき、次の候補を何件まで試すか
MAX_CANDIDATES = 5


def load_log() -> list[dict]:
    return json.loads(LOG_PATH.read_text(encoding="utf-8")) if LOG_PATH.exists() else []


def recently_posted_urls(log: list[dict], days: int = DEDUPE_DAYS) -> set[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    urls = set()
    for entry in log:
        try:
            if datetime.fromisoformat(entry["posted_at"]) >= cutoff:
                urls.add(entry["article_url"])
        except (KeyError, ValueError):
            continue
    return urls


def set_output(name: str, value: str) -> None:
    """GitHub Actions に結果を渡す（ローカル実行時は何もしない）。"""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def append_log(entry: dict) -> None:
    log = load_log()
    log.append(entry)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    candidates = notion_draft.fetch_approved(limit=MAX_CANDIDATES)
    if not candidates:
        print("[skip] approved=0 件、何もしない")
        set_output("stock_out", "true")
        return 0

    recent = recently_posted_urls(load_log())
    draft = None
    for cand in candidates:
        if cand["article_url"] in recent:
            reason = f"直近{DEDUPE_DAYS}日に同じ記事を投稿済み"
            print(f"[dup] {cand['title'][:40]}... → 見送り（{reason}）")
            if not args.dry_run:
                notion_draft.mark_skipped(cand["page_id"], reason)
            continue
        draft = cand
        break

    if not draft:
        print(f"[skip] approved {len(candidates)}件すべて重複、投稿しない")
        set_output("stock_out", "true")
        return 0

    print(f"[picked] {draft['title'][:40]}... (page={draft['page_id']})")

    if args.dry_run:
        for i, p in enumerate(draft["posts"], 1):
            print(f"\n--- post {i} ({len(p)}字) ---\n{p}")
        if draft.get("image_url"):
            print(f"\n[image] {draft['image_url']}")
        return 0

    result = post_threads.post_tree(draft["posts"], draft.get("image_url"))
    print(f"[posted] root_id={result['root_id']}")

    notion_draft.mark_posted(draft["page_id"], result["root_id"])
    print("[notion] status -> posted")

    append_log({
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "article_url": draft["article_url"],
        "article_title": draft["title"],
        "thread_root_id": result["root_id"],
        "post_count": len(draft["posts"]),
        "had_image": bool(draft.get("image_url")),
        "notion_page_id": draft["page_id"],
    })
    print("[log] appended")
    return 0


if __name__ == "__main__":
    sys.exit(main())
