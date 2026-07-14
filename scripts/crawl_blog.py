"""WordPress REST API から記事一覧を取得してキャッシュする。

RSSフィードは最新10件しか返さないため、WP APIで全記事を取得する。
除外カテゴリ（旅行など、Threads投稿に向かない記事）はここで弾く。
"""
from __future__ import annotations

import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

API_URL = "https://chanko06.com/wp-json/wp/v2/posts"
# Threads投稿の対象外にするカテゴリID（chanko06.com: 17=旅行）
EXCLUDE_CATEGORIES = [17]
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "articles_cache.json"


def _clean_url(url: str) -> str:
    """UTMパラメータを取り除いた正規URLを返す。"""
    if not url:
        return url
    parsed = urlparse(url)
    cleaned_qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                  if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunparse(parsed._replace(query=urlencode(cleaned_qs)))


def fetch_feed(url: str = API_URL) -> list[dict]:
    """全記事を新しい順で返す（除外カテゴリを除く）。"""
    exclude = ",".join(str(c) for c in EXCLUDE_CATEGORIES)
    query = f"?per_page=100&categories_exclude={exclude}&_fields=link,title,date"
    req = Request(url + query, headers={"User-Agent": "blog-to-threads/1.0"})
    with urlopen(req, timeout=20) as resp:
        posts = json.loads(resp.read())

    items = []
    for post in posts:
        link = _clean_url((post.get("link") or "").strip())
        if not link:
            continue
        items.append({
            "title": html.unescape(post.get("title", {}).get("rendered", "")).strip(),
            "url": link,
            "pub_date": post.get("date", ""),
            "description": "",
            "content_html": "",
        })
    return items


def save_cache(items: list[dict]) -> None:
    """取得結果を先頭に、取得できなくなった過去記事も後ろに残して蓄積する。"""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    feed_urls = {a["url"] for a in items}
    old_items: list[dict] = []
    if CACHE_PATH.exists():
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8")).get("items", [])
        old_items = [a for a in cached if a["url"] not in feed_urls]
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "items": items + old_items,
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    items = fetch_feed()
    save_cache(items)
    print(f"fetched {len(items)} articles -> {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
