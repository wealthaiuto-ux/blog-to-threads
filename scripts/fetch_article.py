"""記事URLからHTMLを取得し、本文テキストとog:imageを抽出する。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from urllib.request import Request, urlopen


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_article = False
        self.skip_depth = 0
        self.parts: list[str] = []
        self.og_image: str | None = None
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "meta" and attrs_d.get("property") == "og:image":
            self.og_image = attrs_d.get("content")
        if tag == "title":
            self._in_title = True
        if tag == "article":
            self.in_article = True
        if self.in_article and tag in {"script", "style", "noscript", "nav", "aside", "footer", "form"}:
            self.skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "article":
            self.in_article = False
        if self.in_article and tag in {"script", "style", "noscript", "nav", "aside", "footer", "form"} and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data):
        if self._in_title and not self.title:
            self.title = data.strip()
        if self.in_article and self.skip_depth == 0:
            text = data.strip()
            if text:
                self.parts.append(text)


def fetch(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "blog-to-threads/1.0"})
    with urlopen(req, timeout=20) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    p = _Extractor()
    p.feed(html)
    body = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    return {
        "url": url,
        "title": p.title or "",
        "body": body[:4000],
        "og_image": p.og_image,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    args = ap.parse_args()
    print(json.dumps(fetch(args.url), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
