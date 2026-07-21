"""月次レビュー：数字を見て playbook.json の変更案を1つだけ出す。

このスクリプトが「修正」の実体。数字 → weight書き換え → 翌月の投稿が変わる、でループが閉じる。

守っていること:
  - **変更は1箇所だけ**。一度に3つ変えると何が効いたか分からなくなる
  - **サンプルが足りなければ何も提案しない**。「サンプル不足」と明記して保留する
  - 平均ではなく**中央値**で比較する（1本の外れ値で平均が倍以上動くため）
  - 提案するだけ。playbook.json を実際に書き換えるのは --apply（人の承認後）

使い方:
    python scripts/monthly_review.py              # 先月分を集計して提案を出す
    python scripts/monthly_review.py --apply      # 提案を playbook.json に反映する
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
PLAYBOOK = ROOT / "data" / "playbook.json"
HISTORY = ROOT / "data" / "playbook_history.json"

# 1グループあたりこの本数を下回ったら比較に使わない。
# 型は6種あるので、月30本なら1種5本が期待値。5本でも本当は心もとないが、
# ここを上げすぎると永久に何も判断できなくなるため、最低ラインとして5本を置く。
MIN_N = 5
# 中央値がこの倍率以上離れていなければ「差があるとは言えない」として提案しない
MIN_RATIO = 1.5


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def month_range(target: str | None) -> tuple[datetime, datetime, str]:
    """対象月の期間を返す。既定は先月。"""
    now = datetime.now(JST)
    if target:
        year, month = (int(x) for x in target.split("-"))
    else:
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = first_this - timedelta(days=1)
        year, month = last_month.year, last_month.month
    start = datetime(year, month, 1, tzinfo=JST)
    end = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=JST)
    return start, end, f"{year:04d}-{month:02d}"


def group_stats(records: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[int]] = {}
    for r in records:
        k = r.get(key)
        if k:
            groups.setdefault(k, []).append(r["views"])
    return {k: {"n": len(v), "median": st.median(v), "mean": st.mean(v)}
            for k, v in sorted(groups.items())}


def propose(stats: dict[str, dict], weights: dict[str, dict],
            ceilings: dict[str, int] | None = None) -> dict | None:
    """比較可能なグループから、最も差が大きい組み合わせで ±1 の変更案を作る。"""
    usable = {k: v for k, v in stats.items() if v["n"] >= MIN_N and k in weights}
    if len(usable) < 2:
        return None

    best = max(usable, key=lambda k: usable[k]["median"])
    worst = min(usable, key=lambda k: usable[k]["median"])
    if best == worst:
        return None

    hi, lo = usable[best]["median"], usable[worst]["median"]
    if lo <= 0 or hi / lo < MIN_RATIO:
        return None

    up_now = weights[best]["weight"]
    down_now = weights[worst]["weight"]
    if down_now <= 1:
        return None  # これ以上下げると型/テーマが消えてしまう
    if ceilings and up_now + 1 > ceilings.get(best, 10 ** 6):
        return None  # 記事在庫を超えると30日以内に同じ記事が再登場する

    return {
        "up": best, "up_from": up_now, "up_to": up_now + 1,
        "down": worst, "down_from": down_now, "down_to": down_now - 1,
        "ratio": hi / lo, "hi": hi, "lo": lo,
        "n_up": usable[best]["n"], "n_down": usable[worst]["n"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="対象月 YYYY-MM（既定は先月）")
    ap.add_argument("--apply", action="store_true", help="提案を playbook.json に反映する")
    args = ap.parse_args()

    start, end, label = month_range(args.month)
    pb = _load(PLAYBOOK, {})
    insights = [r for r in _load(ROOT / "data" / "insights.json", []) if r.get("status") == "ok"]

    target = []
    for r in insights:
        try:
            t = datetime.fromisoformat(r["posted_at"]).astimezone(JST)
        except (KeyError, ValueError):
            continue
        if start <= t < end and r.get("post_type"):
            target.append(r)

    type_stats = group_stats(target, "post_type")
    theme_stats = group_stats(target, "theme")

    ceilings = {k: v.get("inventory", 10 ** 6) for k, v in pb.get("themes", {}).items()}
    type_prop = propose(type_stats, pb.get("types", {}))
    theme_prop = propose(theme_stats, pb.get("themes", {}), ceilings)

    # 変更は1箇所だけ。差の大きいほうの軸を選ぶ
    axis, prop = None, None
    if type_prop and theme_prop:
        axis, prop = ("types", type_prop) if type_prop["ratio"] >= theme_prop["ratio"] else ("themes", theme_prop)
    elif type_prop:
        axis, prop = "types", type_prop
    elif theme_prop:
        axis, prop = "themes", theme_prop

    lines = [f"# 月次レビュー {label}", "",
             f"対象投稿: **{len(target)}本**（型が記録されているもののみ）", ""]

    for name, stats in (("型", type_stats), ("テーマ", theme_stats)):
        lines += [f"## {name}別の成績", "",
                  f"| {name} | 本数 | views中央値 | 平均 | 判定 |", "|---|---:|---:|---:|---|"]
        if not stats:
            lines.append("| （データなし） | - | - | - | - |")
        for k, v in sorted(stats.items(), key=lambda x: -x[1]["median"]):
            judge = "比較対象" if v["n"] >= MIN_N else f"サンプル不足（{MIN_N}本未満）"
            lines.append(f"| {k} | {v['n']} | {v['median']:.0f} | {v['mean']:.0f} | {judge} |")
        lines.append("")

    lines += ["## 今月の変更案", ""]
    if prop is None:
        lines += [
            "**変更なし（サンプル不足）**", "",
            f"比較には1グループあたり{MIN_N}本以上、かつ中央値に{MIN_ratio_text()}以上の差が必要です。",
            "今月はその条件を満たしませんでした。**これは異常ではありません。**",
            "意味のある差が見えるのは3ヶ月目からです。ここで焦って変えると、",
            "偶然の上下を追いかけて何が効いたのか分からなくなります。",
        ]
    else:
        target_name = "型" if axis == "types" else "テーマ"
        lines += [
            f"**{target_name}の weight を1箇所だけ変更します。**", "",
            f"- `{prop['up']}` : {prop['up_from']} → **{prop['up_to']}**",
            f"- `{prop['down']}` : {prop['down_from']} → **{prop['down_to']}**", "",
            "### 根拠", "",
            f"- `{prop['up']}` の views中央値 {prop['hi']:.0f}（{prop['n_up']}本）",
            f"- `{prop['down']}` の views中央値 {prop['lo']:.0f}（{prop['n_down']}本）",
            f"- 差は **{prop['ratio']:.1f}倍**（判定ライン {MIN_RATIO}倍）", "",
            "### 承認する場合", "",
            "```", "python scripts/monthly_review.py"
            + (f" --month {label}" if args.month else "") + " --apply", "```", "",
            "承認しない場合は何もしなくて構いません。playbook.json は変わりません。",
        ]

    out = ROOT / "reports" / "monthly" / f"{label}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[ok] {out.relative_to(ROOT)}")

    if args.apply:
        if prop is None:
            print("[skip] 変更案がないため何もしません")
            return 0
        pb[axis][prop["up"]]["weight"] = prop["up_to"]
        pb[axis][prop["down"]]["weight"] = prop["down_to"]
        pb["updated_at"] = datetime.now(JST).strftime("%Y-%m-%d")
        pb["updated_by"] = f"monthly-review {label}"
        pb["changed"] = (f"{prop['up']} {prop['up_from']}→{prop['up_to']} / "
                         f"{prop['down']} {prop['down_from']}→{prop['down_to']}"
                         f"（{label}: 中央値 {prop['hi']:.0f} vs {prop['lo']:.0f}）")
        PLAYBOOK.write_text(json.dumps(pb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        history = _load(HISTORY, [])
        history.append({"month": label, "axis": axis, **prop,
                        "applied_at": datetime.now(JST).isoformat()})
        HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[applied] {pb['changed']}")
    return 0


def MIN_ratio_text() -> str:
    return f"{MIN_RATIO}倍"


if __name__ == "__main__":
    raise SystemExit(main())
