"""playbook.json を読み、今日の「型」「テーマ」「記事」を決める。

システムの設定値は playbook.json だけ。生成はここを読み、月次レビューだけが書き換える。
これによって「数字を見た → 何を直せば明日の投稿が変わるのか」が一意になる。

記事選択の方針:
  1. 直近 article_cooldown_days（既定30日）に生成・投稿した記事は除外
  2. 残りのうち **一度も使っていない記事を最優先**
     （旧実装は新着70%の重み付けだけで、35本中15本が一度も使われない一方、
       6本が3回ずつ使われるという偏りを生んでいた）
  3. それでも候補が無ければクールダウンを無視して最も古く使った記事に戻る
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAYBOOK_PATH = ROOT / "data" / "playbook.json"
THEMES_PATH = ROOT / "data" / "article_themes.json"
POSTED_LOG = ROOT / "data" / "posted_log.json"
GEN_LOG = ROOT / "data" / "generated_log.json"


def load_playbook() -> dict:
    if not PLAYBOOK_PATH.exists():
        raise SystemExit("playbook.json が無い")
    return json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))


def load_articles() -> list[dict]:
    if not THEMES_PATH.exists():
        raise SystemExit("article_themes.json が無い。先に classify_articles.py を実行")
    return json.loads(THEMES_PATH.read_text(encoding="utf-8"))


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _weighted_choice(entries: dict[str, dict], exclude: set[str] | None = None) -> str:
    exclude = exclude or set()
    pool = {k: v for k, v in entries.items() if k not in exclude and v.get("weight", 0) > 0}
    if not pool:
        # 全部除外されたらクールダウンを無視して選び直す
        pool = {k: v for k, v in entries.items() if v.get("weight", 0) > 0}
    names = list(pool)
    weights = [pool[n]["weight"] for n in names]
    return random.choices(names, weights=weights, k=1)[0]


def _usage_history() -> list[tuple[str, datetime]]:
    """(article_url, 使った日時) の一覧。生成・投稿の両方を「使った」とみなす。"""
    out: list[tuple[str, datetime]] = []
    # ネタ帳由来のログは article_url=None なので除外する（記事在庫の履歴ではない）
    for entry in _load(POSTED_LOG):
        url = entry.get("article_url")
        if not url:
            continue
        try:
            out.append((url.rstrip("/"), datetime.fromisoformat(entry["posted_at"])))
        except (KeyError, ValueError):
            continue
    for entry in _load(GEN_LOG):
        url = entry.get("article_url")
        if not url:
            continue
        try:
            out.append((url.rstrip("/"), datetime.fromisoformat(entry["generated_at"])))
        except (KeyError, ValueError):
            continue
    return out


def recent_types(days: int) -> set[str]:
    """直近N日に使った型。同じ型が連続するのを避ける。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    used = set()
    for entry in _load(POSTED_LOG) + _load(GEN_LOG):
        ts = entry.get("posted_at") or entry.get("generated_at")
        t = entry.get("post_type")
        if not t or not ts:
            continue
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                used.add(t)
        except ValueError:
            continue
    return used


def _used_combos(days: int) -> set[tuple[str, str]]:
    """直近N日に使った (記事URL, 型) の組み合わせ。

    同じ記事でも型が違えば別コンテンツとして扱うため、除外の単位は記事単体ではなく組み合わせ。
    post_type を持たない古いログは判定に使えないので無視する。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    combos = set()
    for entry in _load(POSTED_LOG) + _load(GEN_LOG):
        ts = entry.get("posted_at") or entry.get("generated_at")
        t = entry.get("post_type")
        url = entry.get("article_url")
        if not (ts and t and url):  # url が None のネタ由来ログはここで弾かれる
            continue
        try:
            if datetime.fromisoformat(ts) >= cutoff:
                combos.add((url.rstrip("/"), t))
        except ValueError:
            continue
    return combos


def pick_article(theme: str, cooldown_days: int, extra_excluded: set[str] | None = None,
                 *, post_type: str | None = None, combo_cooldown_days: int = 180,
                 allow_fallback: bool = True) -> dict | None:
    """テーマ内から記事を1本選ぶ。

    優先順位:
      1. この型でまだ使っていない記事のうち、記事単体も未使用のもの
      2. この型でまだ使っていない記事のうち、記事のクールダウンが明けたもの
      3. この型でまだ使っていない記事の中で、最も長く使っていないもの上位3本
    """
    extra_excluded = {u.rstrip("/") for u in (extra_excluded or set())}
    articles = [a for a in load_articles()
                if a.get("theme") == theme and a["url"].rstrip("/") not in extra_excluded]

    # この型で既に使った記事を除く
    if post_type:
        used = _used_combos(combo_cooldown_days)
        remaining = [a for a in articles if (a["url"].rstrip("/"), post_type) not in used]
        # 全部使い切っていたら組み合わせの制約を落とす（型の中で一周した状態）
        articles = remaining or articles

    if not articles:
        return None

    history = _usage_history()
    last_used: dict[str, datetime] = {}
    for url, ts in history:
        if url not in last_used or ts > last_used[url]:
            last_used[url] = ts

    cutoff = datetime.now(timezone.utc) - timedelta(days=cooldown_days)

    never_used = [a for a in articles if a["url"].rstrip("/") not in last_used]
    if never_used:
        return random.choice(never_used)

    cooled = [a for a in articles if last_used[a["url"].rstrip("/")] < cutoff]
    if cooled:
        return random.choice(cooled)

    # ここまで来たら、このテーマの記事は全部クールダウン中。
    # 呼び出し側に他テーマを試させるため、いったん諦める。
    if not allow_fallback:
        return None

    # どのテーマも詰まっている場合の最終手段。最も長く使っていないもの上位3本から選ぶ
    # （min() で決め打ちすると毎回同じ記事が返ってしまうため）。
    oldest = sorted(articles, key=lambda a: last_used[a["url"].rstrip("/")])[:3]
    return random.choice(oldest)


def check_inventory(pb: dict | None = None) -> list[str]:
    """テーマの weight（30日あたりの本数）が記事在庫を超えていないか検査する。

    超えていると同じ記事が30日以内に再登場する。月次レビューで weight を触った
    ときに気づけるよう、生成時に毎回チェックして警告を出す。
    """
    pb = pb or load_playbook()
    counts: dict[str, int] = {}
    for a in load_articles():
        if a.get("theme"):
            counts[a["theme"]] = counts.get(a["theme"], 0) + 1

    warnings = []
    for theme, conf in pb["themes"].items():
        stock = counts.get(theme, 0)
        if conf.get("weight", 0) > stock:
            warnings.append(
                f"{theme}: weight {conf['weight']} > 在庫 {stock}本"
                f" — 30日以内に同じ記事が再登場します")
    return warnings


def decide(playbook: dict | None = None, *, force_type: str | None = None,
           force_theme: str | None = None) -> dict:
    """今日の型・テーマ・記事を決めて返す。"""
    pb = playbook or load_playbook()
    rules = pb.get("rules", {})

    post_type = force_type or _weighted_choice(
        pb["types"], exclude=recent_types(rules.get("type_cooldown_days", 3)))

    # テーマは、記事が残っているものだけから選ぶ。
    # 1周目はクールダウンを厳守し、どのテーマも詰まっていた場合だけ2周目で妥協する。
    def _try(allow_fallback: bool) -> tuple[str, dict | None]:
        tried: set[str] = set()
        while True:
            th = force_theme or _weighted_choice(pb["themes"], exclude=tried)
            art = pick_article(
                th,
                rules.get("article_cooldown_days", 14),
                post_type=post_type,
                combo_cooldown_days=rules.get("combo_cooldown_days", 180),
                allow_fallback=allow_fallback,
            )
            if art or force_theme:
                return th, art
            tried.add(th)
            if tried >= set(pb["themes"]):
                return th, None

    theme, article = _try(allow_fallback=False)
    if article is None:
        theme, article = _try(allow_fallback=True)
    if article is None:
        raise SystemExit("どのテーマにも使える記事が無い")

    return {
        "post_type": post_type,
        "type_desc": pb["types"][post_type]["desc"],
        "type_example": pb["types"][post_type].get("example", ""),
        "theme": theme,
        "article": article,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="選択ロジックの動作確認")
    ap.add_argument("--n", type=int, default=10, help="何回ぶん試すか")
    args = ap.parse_args()

    for i in range(args.n):
        d = decide()
        art = d["article"]
        print(f"{i+1:2d}. [{d['post_type']}] [{d['theme']}] {art['title'][:40] if art else '(記事なし)'}")
