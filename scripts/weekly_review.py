"""週次レポート：数字を並べるだけ。判断しない。

**このスクリプトは意図的に何も提案しない。**
1週間（7投稿）ではどの型が良いかを判断できるだけのサンプルが集まらない。
週次で判断すると、偶然の上下を追いかけて毎週方針が変わる。
判断は monthly_review.py（月1回・変更は1箇所）でだけ行う。

出力: reports/weekly/YYYY-MM-DD.md
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))


def _load(name: str, default):
    p = ROOT / "data" / name
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def follower_delta(days: int) -> tuple[int | None, int | None, int | None]:
    """(現在, N日前, 差分)。記録が足りなければ None を含む。"""
    snaps = sorted(_load("followers.json", []), key=lambda r: r["date"])
    if not snaps:
        return None, None, None
    latest = snaps[-1]
    target = (datetime.now(JST) - timedelta(days=days)).strftime("%Y-%m-%d")
    past = next((s for s in snaps if s["date"] >= target), None)
    if past is None or past["date"] == latest["date"]:
        return latest["followers"], None, None
    return latest["followers"], past["followers"], latest["followers"] - past["followers"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    now = datetime.now(JST)
    since = now - timedelta(days=args.days)

    insights = [r for r in _load("insights.json", []) if r.get("status") == "ok"]
    recent = []
    for r in insights:
        try:
            if datetime.fromisoformat(r["posted_at"]).astimezone(JST) >= since:
                recent.append(r)
        except (KeyError, ValueError):
            continue

    cur, past, delta = follower_delta(args.days)

    lines = [
        f"# 週次レポート {now:%Y-%m-%d}",
        "",
        "**このレポートは数字を並べるだけです。読まなくても構いません。**",
        "判断は月末の monthly_review でだけ行います。",
        "",
        "## フォロワー",
        "",
    ]
    if cur is None:
        lines.append("- 記録なし")
    elif delta is None:
        lines += [f"- 現在: **{cur}人**",
                  f"- 前週比: 記録が{args.days}日ぶん貯まっていないため、まだ出せません"]
    else:
        sign = "+" if delta >= 0 else ""
        lines += [f"- 現在: **{cur}人**", f"- {args.days}日前: {past}人",
                  f"- 増減: **{sign}{delta}人**"]

    lines += ["", f"## 直近{args.days}日の投稿", ""]
    if not recent:
        lines.append("- 対象期間に投稿なし（48時間経過したものだけを集計しています）")
    else:
        views = [r["views"] for r in recent]
        lines += [
            f"- 本数: {len(recent)}本",
            f"- views 中央値: **{st.median(views):.0f}** ／ 平均 {st.mean(views):.0f}"
            f" ／ 最小 {min(views)} ／ 最大 {max(views)}",
            f"- いいね合計: {sum(r['likes'] for r in recent)}"
            f" ／ リプライ合計: {sum(r['replies'] for r in recent)}",
            "",
            "| 投稿日 | 型 | テーマ | views | ♥ | 💬 |",
            "|---|---|---|---:|---:|---:|",
        ]
        for r in sorted(recent, key=lambda r: r["posted_at"]):
            d = datetime.fromisoformat(r["posted_at"]).astimezone(JST)
            lines.append(
                f"| {d:%m/%d} | {r.get('post_type') or '-'} | {r.get('theme') or '-'} "
                f"| {r['views']} | {r['likes']} | {r['replies']} |")

    lines += ["", "## 記録の健全性", ""]
    snaps = _load("followers.json", [])
    lines.append(f"- フォロワー記録: {len(snaps)}日ぶん")
    lines.append(f"- insights 取得済み: {len(insights)}件"
                 f"（うち型が記録されているもの: {sum(1 for r in insights if r.get('post_type'))}件）")
    no_type = sum(1 for r in insights if not r.get("post_type"))
    if no_type:
        lines.append(f"- ※ {no_type}件は型が未記録（v3以前の投稿）。型別の比較には使えません")

    out = args.out or ROOT / "reports" / "weekly" / f"{now:%Y-%m-%d}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] {out.relative_to(ROOT)}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
