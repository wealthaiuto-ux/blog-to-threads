#!/usr/bin/env python3
"""共有したURLごとのクリック数を日次で記録する。

2026-08-20追加。Threads の User Insights には `clicks` があり、共有したURL単位で
クリック数が返る（投稿単位では返らない）。投稿ごとに固有の utm_content を振っておけば、
URLの違いがそのまま投稿の違いになるので、投稿別のクリックを自動で突き合わせられる。

APIは「指定した期間の合計」を返す。日別の内訳は返らないので、毎日1回
「昨日ぶん」を問い合わせて自分で積む。ここを止めると、その日のクリックは
二度と取れない（フォロワー数と同じ性質）。

  python3 scripts/collect_clicks.py              # 昨日ぶんを記録
  python3 scripts/collect_clicks.py --days 30    # 直近30日の合計を確認（記録しない）
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen

GRAPH = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))
OUT = Path(__file__).resolve().parent.parent / "data" / "clicks.json"


def fetch_clicks(user_id: str, token: str, since: int, until: int) -> dict[str, int]:
    qs = urlencode({"metric": "clicks", "since": since, "until": until, "access_token": token})
    try:
        with urlopen(f"{GRAPH}/{user_id}/threads_insights?{qs}", timeout=30) as r:
            body = json.loads(r.read())
    except HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise SystemExit(f"Threads API error {e.code}: {detail}")

    for entry in body.get("data", []):
        if entry.get("name") != "clicks":
            continue
        return {v["link_url"]: int(v.get("value", 0))
                for v in entry.get("link_total_values", []) if v.get("link_url")}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, help="この日数ぶんの合計を表示するだけ（記録しない）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("THREADS_ACCESS_TOKEN")
    user_id = os.environ.get("THREADS_USER_ID")
    if not token or not user_id:
        raise SystemExit("THREADS_ACCESS_TOKEN / THREADS_USER_ID が未設定")

    now = int(time.time())
    if args.days:
        urls = fetch_clicks(user_id, token, now - args.days * 86400, now)
        hit = {u: v for u, v in urls.items() if v}
        print(f"直近{args.days}日: URL {len(urls)}本 / 合計 {sum(urls.values())}クリック",
              file=sys.stderr)
        for u, v in sorted(hit.items(), key=lambda x: -x[1]):
            print(f"  {v:>4}  {u}", file=sys.stderr)
        return

    # 「昨日」を JST の1日として問い合わせる
    today = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = today - timedelta(days=1), today
    urls = fetch_clicks(user_id, token, int(start.timestamp()), int(end.timestamp()))
    date = start.strftime("%Y-%m-%d")
    hit = {u: v for u, v in urls.items() if v}
    print(f"{date}: URL {len(urls)}本 / クリックのあったURL {len(hit)}本 / "
          f"合計 {sum(urls.values())}", file=sys.stderr)

    if args.dry_run:
        print("[dry-run] 書き込まない", file=sys.stderr)
        return

    records = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else []
    # 同じ日を二度書かない（0件の日も「観測した」記録として残す）
    records = [r for r in records if r.get("date") != date]
    records.append({"date": date, "total": sum(urls.values()), "urls": hit})
    records.sort(key=lambda r: r["date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] {OUT.name} に追記（通算{len(records)}日ぶん）", file=sys.stderr)


if __name__ == "__main__":
    main()
