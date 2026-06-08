"""投稿ジョブ：Notion から status=approved の最古ページを1件取り、Threadsに投稿。

approvedが0件のときは何もせず終了（在庫切れスキップ）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notion_draft  # type: ignore
import post_threads  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "posted_log.json"


def append_log(entry: dict) -> None:
    log = json.loads(LOG_PATH.read_text(encoding="utf-8")) if LOG_PATH.exists() else []
    log.append(entry)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    draft = notion_draft.fetch_oldest_approved()
    if not draft:
        print("[skip] approved=0 件、何もしない")
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
