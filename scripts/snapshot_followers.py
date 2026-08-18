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


def fetch_profile_views(user_id: str, token: str) -> dict[str, int]:
    """アカウント単位の views（＝プロフィールが見られた回数）を日別で取る。

    2026-08-19 追加。投稿ビュー → プロフィール表示 → フォロー のファネルのうち、
    真ん中がこれ。KPIがフォロワーである以上、投稿ビューより後段のこの数字の方が
    フォローに近い先行指標になる。

    日別の時系列で返り、end_time は「その24時間が終わる時刻」（UTC 07:00 = JST 16:00）。
    厳密には前日16時〜当日16時の集計なので、日付ラベルは end_time の日付を採用する。
    """
    qs = urlencode({"metric": "views", "period": "day", "access_token": token})
    url = f"{GRAPH}/{user_id}/threads_insights?{qs}"
    try:
        with urlopen(url, timeout=30) as resp:
            body = json.loads(resp.read())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        print(f"[warn] プロフィール表示の取得に失敗（{e.code}）: {detail}", file=sys.stderr)
        return {}

    out: dict[str, int] = {}
    for entry in body.get("data", []):
        if entry.get("name") != "views":
            continue
        for v in entry.get("values") or []:
            end = v.get("end_time") or ""
            if end and "value" in v:
                out[end[:10]] = int(v["value"])
    return out


def merge_profile_views(path: Path, views_by_date: dict[str, int]) -> int:
    """取得できた日別プロフィール表示を、既存レコードに後から埋める。

    フォロワー数と違い数日分まとめて返るので、過去分の穴も自然に埋まる。
    """
    if not views_by_date or not path.exists():
        return 0
    records = json.loads(path.read_text(encoding="utf-8"))
    filled = 0
    for r in records:
        v = views_by_date.get(r.get("date"))
        if v is not None and r.get("profile_views") != v:
            r["profile_views"] = v
            filled += 1
    if filled:
        path.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return filled


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

    views_by_date = fetch_profile_views(user_id, token)
    if views_by_date:
        latest = sorted(views_by_date)[-1]
        print(f"profile_views = {views_by_date[latest]}（{latest} 時点 / {len(views_by_date)}日分取得）",
              file=sys.stderr)

    if args.dry_run:
        print("[dry-run] ファイルには書き込まない", file=sys.stderr)
        return

    written, record = append_snapshot(args.out, followers)
    filled = merge_profile_views(args.out, views_by_date)
    if filled:
        print(f"[ok] プロフィール表示を{filled}日分反映", file=sys.stderr)
    if written:
        print(f"[ok] 追記: {record['date']} -> {record['followers']}", file=sys.stderr)
    else:
        print(f"[skip] {record['date']} は記録済み（{record['followers']}）", file=sys.stderr)


if __name__ == "__main__":
    main()
