"""Threads のフォロワー数を1日1回スナップショットする。

フォロワー数は「その時点の値」しか取れず、過去に遡れない。
記録を始めた日が観測の起点になるため、投稿を止めている期間も動かし続ける。

出力: data/followers.json に1日1行を追記（同じ日付が既にあれば上書きしない）
    [{"date": "2026-07-22", "followers": 123, "fetched_at": "..."} , ...]

ref: https://developers.facebook.com/docs/threads/insights
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

GRAPH = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "followers.json"


def fetch_followers(user_id: str, token: str) -> int:
    """threads_insights の followers_count を取る。

    followers_count は /me?fields= では取れず、insights 側の total_value で返る。
    """
    qs = urlencode({"metric": "followers_count", "access_token": token})
    url = f"{GRAPH}/{user_id}/threads_insights?{qs}"
    try:
        with urlopen(url, timeout=30) as resp:
            body = json.loads(resp.read())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise SystemExit(f"Threads API error {e.code}: {detail}")

    for entry in body.get("data", []):
        if entry.get("name") == "followers_count":
            tv = entry.get("total_value")
            if isinstance(tv, dict) and "value" in tv:
                return int(tv["value"])
            # 念のため values 形式（time series）にも対応
            values = entry.get("values") or []
            if values and "value" in values[-1]:
                return int(values[-1]["value"])
    raise SystemExit(f"followers_count が応答に含まれていない: {json.dumps(body, ensure_ascii=False)[:300]}")


def append_snapshot(path: Path, followers: int) -> tuple[bool, dict]:
    """同じ日付の記録が既にあれば追記しない。戻り値は (追記したか, その日のレコード)。"""
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    records: list[dict] = []
    if path.exists():
        records = json.loads(path.read_text(encoding="utf-8"))

    for r in records:
        if r.get("date") == today:
            return False, r

    record = {"date": today, "followers": followers, "fetched_at": now.isoformat()}
    records.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True, record


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--token-env", default="THREADS_ACCESS_TOKEN",
                    help="アクセストークンを持つ環境変数名（検証時に別アカウントを指定するため）")
    ap.add_argument("--user-env", default="THREADS_USER_ID")
    ap.add_argument("--dry-run", action="store_true", help="取得だけしてファイルに書かない")
    args = ap.parse_args()

    token = os.environ.get(args.token_env)
    user_id = os.environ.get(args.user_env)
    if not token or not user_id:
        raise SystemExit(f"{args.token_env} / {args.user_env} が未設定")

    followers = fetch_followers(user_id, token)
    print(f"followers_count = {followers}", file=sys.stderr)

    if args.dry_run:
        print("[dry-run] ファイルには書き込まない", file=sys.stderr)
        return

    written, record = append_snapshot(args.out, followers)
    if written:
        print(f"[ok] 追記: {record['date']} -> {record['followers']}", file=sys.stderr)
    else:
        print(f"[skip] {record['date']} は記録済み（{record['followers']}）", file=sys.stderr)


if __name__ == "__main__":
    main()
