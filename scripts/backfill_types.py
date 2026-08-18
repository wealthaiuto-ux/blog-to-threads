#!/usr/bin/env python3
"""過去の投稿に型・テーマを後から埋める（1回だけ使う復旧用）。

2026-08-19。投稿時に型・テーマをログへ残していなかったため、insights 42件すべてで
post_type / theme が null になっていた。ただし posted_log には notion_page_id が
全件残っており、Notion 側には生成時の「型」「テーマ」がプロパティとして保存されている。
つまり過去分は復元できる。

これを流すと、42件が「どの型が効いたか」の集計に使えるようになる。
以後の投稿は run_post.py が自動でログに残すので、このスクリプトは不要。

  python3 scripts/backfill_types.py --dry-run   # 何が埋まるかだけ見る
  python3 scripts/backfill_types.py             # 実際に書き込む
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "posted_log.json"
INSIGHTS_PATH = ROOT / "data" / "insights.json"
NOTION = "https://api.notion.com/v1"


def fetch_page(page_id: str, key: str) -> dict | None:
    req = Request(
        f"{NOTION}/pages/{page_id}",
        headers={"Authorization": f"Bearer {key}", "Notion-Version": "2022-06-28"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:160]
        print(f"[ng] {page_id}: {e.code} {detail}", file=sys.stderr)
        return None


def read_select(props: dict, name: str) -> str | None:
    sel = props.get(name, {}).get("select")
    return sel.get("name") if isinstance(sel, dict) else None


def read_note(page_id: str, key: str) -> tuple[str | None, str | None]:
    """ページ本文の「型: ○○ ／ テーマ: ○○」から拾う。

    Notion DB に「型」「テーマ」のプロパティが無かった時期は、プロパティが空のまま
    本文ノートにだけ残っている。過去分の復元はこちらが本命。
    """
    req = Request(
        f"{NOTION}/blocks/{page_id}/children?page_size=50",
        headers={"Authorization": f"Bearer {key}", "Notion-Version": "2022-06-28"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except HTTPError as e:
        print(f"[ng] blocks {page_id}: {e.code}", file=sys.stderr)
        return None, None

    import re
    for block in body.get("results", []):
        rt = (block.get(block.get("type", ""), {}) or {}).get("rich_text") or []
        text = "".join(t.get("plain_text", "") for t in rt)
        m = re.search(r"型[:：]\s*(\S+?)\s*[／/]\s*テーマ[:：]\s*(\S+)", text)
        if m:
            return m.group(1), m.group(2)
    return None, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("NOTION_API_KEY")
    if not key:
        raise SystemExit("NOTION_API_KEY が未設定")

    log = json.loads(LOG_PATH.read_text(encoding="utf-8"))
    insights = json.loads(INSIGHTS_PATH.read_text(encoding="utf-8"))
    by_thread = {r.get("thread_id"): r for r in insights}

    filled = skipped = failed = 0
    for entry in log:
        if entry.get("post_type") and entry.get("theme"):
            skipped += 1
            continue
        page_id = entry.get("notion_page_id")
        if not page_id:
            failed += 1
            continue

        page = fetch_page(page_id, key)
        time.sleep(0.35)          # Notion は 3req/秒 程度が上限
        if not page:
            failed += 1
            continue

        props = page.get("properties", {})
        ptype, theme = read_select(props, "型"), read_select(props, "テーマ")
        if not ptype and not theme:
            # プロパティが無い時期のページは、本文ノートから拾う
            ptype, theme = read_note(page_id, key)
            time.sleep(0.35)
        if not ptype and not theme:
            print(f"[--] {entry.get('article_title', '')[:26]}: 型が見つからない", file=sys.stderr)
            failed += 1
            continue

        entry["post_type"], entry["theme"] = ptype, theme
        rec = by_thread.get(entry.get("thread_root_id"))
        if rec is not None:
            rec["post_type"], rec["theme"] = ptype, theme
        filled += 1
        print(f"[ok] {ptype} / {theme} ← {entry.get('article_title', '')[:26]}", file=sys.stderr)

    print(f"\n埋めた {filled} 件 / 既に有り {skipped} 件 / 取れず {failed} 件", file=sys.stderr)

    if args.dry_run:
        print("[dry-run] 書き込まない", file=sys.stderr)
        return
    if filled:
        LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        INSIGHTS_PATH.write_text(json.dumps(insights, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("[ok] posted_log.json と insights.json を更新", file=sys.stderr)


if __name__ == "__main__":
    main()
