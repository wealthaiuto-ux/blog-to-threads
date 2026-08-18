#!/usr/bin/env python3
"""却下された案を data/rejections.json に貯める。

2026-08-19。承認（approved）だけを読んで却下（cancelled）を読み捨てていたため、
「これは自分じゃない」という本人の判断が生成に一切戻っていなかった。
出版アカウント側（@uto_ai_publisher）には翌晩に却下理由を読み返す手順があるので、
その仕組みをこちらへ移植する。

Notion のページはいつ消えるか分からないので、こちらにも写しておく。
生成時は generate_post.py がこのファイルを読む。

  python3 scripts/collect_rejections.py --dry-run
  python3 scripts/collect_rejections.py
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notion_draft  # type: ignore

OUT = Path(__file__).resolve().parent.parent / "data" / "rejections.json"
KEEP = 60          # 直近60件だけ保持する。古い却下は好みの変化で意味が薄れる


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = notion_draft.fetch_cancelled(limit=args.limit)
    existing = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    known = {r.get("page_id") for r in existing}

    added = []
    for r in rows:
        if r["page_id"] in known:
            continue
        r["collected_at"] = datetime.now(timezone.utc).isoformat()
        added.append(r)

    if not added:
        print(f"[skip] 新しい却下はありません（保存済み {len(existing)}件）", file=sys.stderr)
        return

    for r in added:
        reason = r.get("reason") or "（理由の記入なし）"
        print(f"[+] {r.get('post_type') or '型なし'}: {reason[:40]}", file=sys.stderr)

    if args.dry_run:
        print(f"[dry-run] {len(added)}件を書き込まない", file=sys.stderr)
        return

    merged = (added + existing)[:KEEP]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] {len(added)}件追加（合計 {len(merged)}件）", file=sys.stderr)


if __name__ == "__main__":
    main()
