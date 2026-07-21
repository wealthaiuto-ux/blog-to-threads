"""生成ジョブ（v3）：playbook が決めた型・テーマ・記事で単発投稿を1本作り、Notionに保存する。

v2 までとの違い:
  - ツリー生成 → 単発生成（generate_tree.py ではなく generate_post.py）
  - 記事選択が「新着70%のランダム」→ playbook.py（未使用優先・型との組み合わせ管理）
  - 生成物を機械フィルタに通し、通らなければ needs_fix で保存して人には出さない
  - generated_log に post_type / theme を記録する（月次レビューの比較軸になる）

設計の全体像は REDESIGN.md、ルールは CLAUDE.md を参照。
旧ツリー版は generate_tree.py に残してあるが、v3 では使っていない。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crawl_blog  # type: ignore
import fetch_article  # type: ignore
import generate_post  # type: ignore
import notion_draft  # type: ignore
import playbook  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
GEN_LOG_PATH = ROOT / "data" / "generated_log.json"


def _append_gen_log(entry: dict) -> None:
    log = []
    if GEN_LOG_PATH.exists():
        try:
            log = json.loads(GEN_LOG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log = []
    log.append(entry)
    GEN_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1, help="生成本数（既定1。1日1本の運用）")
    ap.add_argument("--type", dest="force_type", help="型を指定（検証用）")
    ap.add_argument("--theme", dest="force_theme", help="テーマを指定（検証用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="Notionに保存せず、生成結果を標準出力に出すだけ")
    args = ap.parse_args()

    pb = playbook.load_playbook()
    for w in playbook.check_inventory(pb):
        print(f"[warn] {w}", file=sys.stderr)

    # 記事キャッシュを更新し、分類ファイルに無い新着があれば知らせる
    items = crawl_blog.fetch_feed()
    crawl_blog.save_cache(items)
    known = {a["url"].rstrip("/") for a in playbook.load_articles()}
    unknown = [a for a in items if a["url"].rstrip("/") not in known]
    if unknown:
        print(f"[warn] 未分類の新着記事が{len(unknown)}本あります。"
              f"classify_articles.py を実行してください", file=sys.stderr)
        for a in unknown[:5]:
            print(f"        - {a['title'][:50]}", file=sys.stderr)

    used_urls: set[str] = set()
    for i in range(args.count):
        decision = playbook.decide(pb, force_type=args.force_type, force_theme=args.force_theme)
        meta = decision["article"]
        if meta["url"].rstrip("/") in used_urls:
            print(f"[skip] 同一バッチ内で重複（{meta['title'][:30]}）", file=sys.stderr)
            continue
        used_urls.add(meta["url"].rstrip("/"))

        article = fetch_article.fetch(meta["url"])
        result = generate_post.generate(
            decision, article, max_retry=pb.get("rules", {}).get("max_filter_retry", 3))

        header = f"[{decision['post_type']}] [{decision['theme']}] {article['title'][:36]}"
        if args.dry_run:
            print(f"\n=== {header} ===")
            print(f"status: {result['status']}  attempts: {result['attempts']}")
            if result["problems"]:
                print(f"problems: {' / '.join(result['problems'])}")
            print("-" * 40)
            print(result["text"])
            print("-" * 40)
            continue

        page_id = notion_draft.save_single(
            article, result["text"],
            post_type=decision["post_type"],
            theme=decision["theme"],
            status=result["status"],
            problems=result["problems"],
        )
        _append_gen_log({
            "article_url": meta["url"],
            "article_title": article["title"],
            "post_type": decision["post_type"],
            "theme": decision["theme"],
            "status": result["status"],
            "attempts": result["attempts"],
            "notion_page_id": page_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
        mark = "⚠ needs_fix" if result["status"] == "needs_fix" else "saved"
        print(f"[{mark}] {header} -> page={page_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
