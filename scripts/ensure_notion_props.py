#!/usr/bin/env python3
"""Notion DB に「型」「テーマ」プロパティが無ければ作る。

2026-08-19。notion_draft.save_single は「Notion側にプロパティがあるときだけ書き込む」
設計になっている。ところが実際の DB には両方とも存在せず、生成のたびに型・テーマが
黙って捨てられていた。エラーも出ないので誰も気づけない。

プロパティさえ在れば、以後は生成→Notion→投稿ログ→insights と型が流れる。
1回流せば十分だが、何度流しても既存があればスキップするので安全。

  python3 scripts/ensure_notion_props.py --dry-run
  python3 scripts/ensure_notion_props.py
"""
import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

NOTION = "https://api.notion.com/v1"
DEFAULT_DB = "0f4390afe11b45049c0f3639d9004ab0"

# playbook.json の型と、テーマweightに対応させる。
# select の選択肢は後から増やせるので、ここでは現行ぶんだけ入れておく。
TYPES = ["実測値型", "向いてない型", "失敗談型", "質問型", "逆張り型", "1コマ型"]
THEMES = ["家電", "お金", "住まい", "育児"]


def api(method: str, path: str, key: str, payload: dict | None = None) -> dict:
    req = Request(
        f"{NOTION}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={
            "Authorization": f"Bearer {key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        raise SystemExit(f"Notion API error {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=os.environ.get("NOTION_DATABASE_ID") or DEFAULT_DB)
    args = ap.parse_args()

    key = os.environ.get("NOTION_API_KEY")
    if not key:
        raise SystemExit("NOTION_API_KEY が未設定")

    db = api("GET", f"/databases/{args.db}", key)
    existing = db.get("properties", {})
    print("現在のプロパティ:", ", ".join(sorted(existing)), file=sys.stderr)

    want = {
        "型": {"select": {"options": [{"name": t} for t in TYPES]}},
        "テーマ": {"select": {"options": [{"name": t} for t in THEMES]}},
        # 2026-08-19 追加。却下した理由を書く欄。空でも構わないが、一言あると
        # 次の生成が同じ失敗を繰り返さなくなる。
        "却下理由": {"rich_text": {}},
    }
    missing = {k: v for k, v in want.items() if k not in existing}
    if not missing:
        print("[skip] 「型」「テーマ」は既にあります", file=sys.stderr)
        return

    print("追加するプロパティ:", ", ".join(missing), file=sys.stderr)
    if args.dry_run:
        print("[dry-run] 変更しない", file=sys.stderr)
        return

    api("PATCH", f"/databases/{args.db}", key, {"properties": missing})
    print(f"[ok] {len(missing)}件のプロパティを追加しました", file=sys.stderr)


if __name__ == "__main__":
    main()
