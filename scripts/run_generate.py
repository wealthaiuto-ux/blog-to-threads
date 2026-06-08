"""生成ジョブ：記事選定 → ツリー生成 → Notionに status=draft で保存。

これだけ動かして、ユーザーがNotionでレビュー → approved にすると、
run_post.py が朝/昼/夜のスケジュールで拾って投稿する。
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crawl_blog  # type: ignore
import fetch_article  # type: ignore
import generate_tree  # type: ignore
import maybe_image  # type: ignore
import notion_draft  # type: ignore
import pick_article  # type: ignore

# Nano Banana 生成画像を添付する確率（1.0 = 毎回）
IMAGE_PROBABILITY = 1.0
# 画像生成のリトライ回数（Nano Bananaが空返答した場合）
IMAGE_RETRY = 3


def _build_raw_url(local_path: Path) -> str | None:
    """生成画像のローカルパスを GitHub raw URL に変換。
    GitHub Actions では GITHUB_REPOSITORY が自動で設定される。
    ローカル実行時は RAW_BASE_URL 環境変数で上書き可能。
    """
    raw_base = os.environ.get("RAW_BASE_URL")
    if raw_base:
        return f"{raw_base.rstrip('/')}/{local_path.name}"
    repo = os.environ.get("GITHUB_REPOSITORY")  # "owner/repo"
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        return None
    rel = local_path.relative_to(Path(__file__).resolve().parent.parent)
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel.as_posix()}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--article-url", help="特定記事を指定")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--count", type=int, default=1, help="一度に生成するドラフト数")
    args = ap.parse_args()

    items = crawl_blog.fetch_feed()
    crawl_blog.save_cache(items)
    print(f"[crawl] {len(items)} articles")

    excluded = pick_article.recently_posted_urls(pick_article.load_log())

    for i in range(args.count):
        if args.article_url and i == 0:
            meta = next((a for a in items if a["url"] == args.article_url), None)
            if not meta:
                raise SystemExit(f"指定URL {args.article_url} 見つからず")
        else:
            meta = pick_article.pick(items, excluded)
            if not meta:
                print(f"[skip] 在庫切れ（{i}/{args.count}）")
                break
            excluded.add(meta["url"])

        article = fetch_article.fetch(meta["url"])
        tree = generate_tree.call_claude(article)

        image_url = None
        if not args.no_image and random.random() < IMAGE_PROBABILITY:
            out = maybe_image.OUT_DIR / f"thread_{int(time.time())}_{i}.png"
            ok = False
            for attempt in range(1, IMAGE_RETRY + 1):
                ok = maybe_image.generate(
                    tree.get("image_prompt") or article["title"],
                    out,
                    infographic=tree.get("infographic"),
                )
                if ok:
                    break
                print(f"[image] 失敗 attempt {attempt}/{IMAGE_RETRY} → リトライ")
            if ok:
                image_url = _build_raw_url(out)
                if image_url:
                    print(f"[image] {image_url}")
                else:
                    print("[image] 生成済みだが raw URL を組めない（ローカル実行？）")
                    image_url = None
            else:
                print(f"[image] {IMAGE_RETRY}回失敗 → 画像なしで続行")

        page_id = notion_draft.save_draft(article, tree["posts"], image_url)
        print(f"[saved] {meta['title'][:40]}... -> page={page_id}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
