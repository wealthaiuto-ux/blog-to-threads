"""RSS から記事一覧を取得してキャッシュする。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET


def _clean_url(url: str) -> str:
    """RSS由来のUTMパラメータを取り除いた正規URLを返す。"""
    if not url:
        return url
    parsed = urlparse(url)
    cleaned_qs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                  if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunparse(parsed._replace(query=urlencode(cleaned_qs)))

FEED_URL = "https://chanko06.com/feed/"
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "articles_cache.json"


def fetch_feed(url: str = FEED_URL) -> list[dict]:
    req = Request(url, headers={"User-Agent": "blog-to-threads/1.0"})
    with urlopen(req, timeout=20) as resp:
        body = resp.read()

    root = ET.fromstring(body)
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = _clean_url((item.findtext("link") or "").strip())
        pub_date = (item.findtext("pubDate") or "").strip()
        description = (item.findtext("description") or "").strip()
        content_encoded = item.findtext("content:encoded", default="", namespaces=ns).strip()
        if not link:
            continue
        items.append({
            "title": title,
            "url": link,
            "pub_date": pub_date,
            "description": description,
            "content_html": content_encoded,
        })
    return items


def save_cache(items: list[dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    items = fetch_feed()
    save_cache(items)
    print(f"fetched {len(items)} articles -> {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
