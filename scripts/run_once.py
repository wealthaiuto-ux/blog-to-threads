"""1回分のThreadsツリー投稿をまとめて実行する。

flow:
  1. RSSクロール → キャッシュ更新
  2. 投稿対象記事を選定（--article-url で指定も可）
  3. 記事本文を取得
  4. Claude API でツリー本文生成
  5. 30%確率で画像生成 → og:image にフォールバック
  6. Threads ツリー投稿（--dry-run なら投稿せず内容表示のみ）
  7. posted_log.json に記録
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

# 同ディレクトリの兄弟モジュールをimport
sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_blog  # type: ignore
import fetch_article  # type: ignore
import generate_tree  # type: ignore
import maybe_image  # type: ignore
import pick_article  # type: ignore
import post_threads  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "data" / "posted_log.json"
IMAGE_PROBABILITY = 0.3


def append_log(entry: dict) -> None:
    log = json.loads(LOG_PATH.read_text(encoding="utf-8")) if LOG_PATH.exists() else []
    log.append(entry)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="投稿せず内容のみ表示")
    ap.add_argument("--article-url", help="特定記事を指定")
    ap.add_argument("--no-image", action="store_true", help="画像差し込みを無効化")
    args = ap.parse_args()

    # 1. RSSクロール
    items = crawl_blog.fetch_feed()
    crawl_blog.save_cache(items)
    print(f"[1] crawled {len(items)} articles")

    # 2. 記事選定
    if args.article_url:
        article_meta = next((a for a in items if a["url"] == args.article_url), None)
        if not article_meta:
            raise SystemExit(f"指定URL {args.article_url} が見つからない")
    else:
        excluded = pick_article.recently_posted_urls(pick_article.load_log())
        article_meta = pick_article.pick(items, excluded)
        if not article_meta:
            raise SystemExit("投稿可能な記事が無い")
    print(f"[2] picked: {article_meta['title']}")

    # 3. 本文取得
    article = fetch_article.fetch(article_meta["url"])
    print(f"[3] fetched body ({len(article['body'])}字)")

    # 4. ツリー生成
    tree = generate_tree.call_claude(article)
    print(f"[4] generated {len(tree['posts'])}-post tree")

    # 5. 画像（30%確率）
    image_url: str | None = None
    if not args.no_image and random.random() < IMAGE_PROBABILITY:
        # 優先順位: og:image（公開URLあり） > Nano Banana生成（要アップロード機構）
        if article.get("og_image"):
            image_url = article["og_image"]
            print(f"[5] using og:image: {image_url}")
        else:
            # TODO: 生成画像のアップロード機構が必要（GitHub Pages / R2 など）
            print("[5] og:image無し → 画像なしで投稿")

    payload = {"posts": tree["posts"], "image_url": image_url}

    # 6. 投稿（or dry-run）
    if args.dry_run:
        print("\n=== DRY RUN ===")
        for i, p in enumerate(tree["posts"], 1):
            print(f"\n--- post {i} ({len(p)}字) ---\n{p}")
        if image_url:
            print(f"\n[image] {image_url}")
        return 0

    result = post_threads.post_tree(payload["posts"], payload["image_url"])
    print(f"[6] posted: root_id={result['root_id']}")

    # 7. ログ
    append_log({
        "posted_at": datetime.now(timezone.utc).isoformat(),
        "article_url": article_meta["url"],
        "article_title": article_meta["title"],
        "thread_root_id": result["root_id"],
        "post_count": len(tree["posts"]),
        "had_image": bool(image_url),
    })
    print("[7] logged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
