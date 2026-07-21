"""記事をテーマクラスタに分類し、data/article_themes.json に保存する。

playbook.json の themes weight でテーマを選ぶため、記事側にテーマの札が要る。
分類はキーワード規則で行い、結果はファイルに残して人が直せるようにする
（自動判定を毎回走らせると、直した内容が上書きされて消えるため）。

新しい記事が増えたら再実行する。既存の分類は上書きしない。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "article_themes.json"
WP_API = "https://chanko06.com/wp-json/wp/v2/posts?per_page=100&_fields=title,link,date"

# 上から順に判定する。順序が重要:
# 「ふるさと納税 子育て世帯におすすめ」は 子育て を含むが 電力・お金 に入れたいので、
# 育児より先に 電力・お金 を判定する。
RULES: list[tuple[str, list[str]]] = [
    ("対象外", ["フランス旅行", "お出かけ", "GW"]),
    ("電力・お金", ["オクトパス", "電気代", "電力", "中部電力", "ふるさと納税",
                    "住宅ローン", "不動産", "返礼品"]),
    ("育児", ["ベビー", "哺乳瓶", "ミルク", "バウンサー", "ベビーバス", "ヤギ",
              "バブズ", "リッチェル", "Bellababy", "ビーンスターク"]),
    ("住まい", ["マットレス", "NELL", "シャワーヘッド", "ミスト", "HIHO", "マイトレックス"]),
    ("家電", ["エアコン", "乾太くん", "ラムダッシュ", "パームイン", "シェーバー",
              "アイスハグ", "ドラム式"]),
]


def classify(title: str) -> str | None:
    for theme, keywords in RULES:
        if any(k in title for k in keywords):
            return theme
    return None


def fetch_posts() -> list[dict]:
    with urlopen(WP_API, timeout=30) as resp:
        return json.loads(resp.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="既存の分類も再判定して上書きする（手で直した内容は消える）")
    args = ap.parse_args()

    existing: dict[str, dict] = {}
    if OUT_PATH.exists():
        existing = {e["url"]: e for e in json.loads(OUT_PATH.read_text(encoding="utf-8"))}

    out: list[dict] = []
    unclassified: list[str] = []
    for p in fetch_posts():
        title = re.sub(r"<[^>]+>", "", p["title"]["rendered"])
        url = p["link"]
        if not args.force and url in existing:
            out.append(existing[url])
            continue
        theme = classify(title)
        if theme is None:
            unclassified.append(title)
        out.append({"url": url, "title": title, "date": p["date"][:10], "theme": theme})

    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    counts: dict[str, int] = {}
    for e in out:
        counts[e["theme"] or "(未分類)"] = counts.get(e["theme"] or "(未分類)", 0) + 1
    print(f"[ok] {len(out)}本 -> {OUT_PATH.name}")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")
    if unclassified:
        print("\n[要確認] 分類できなかった記事:")
        for t in unclassified:
            print(f"  - {t}")
        print("  → RULES にキーワードを足すか、article_themes.json を直接編集してください")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
