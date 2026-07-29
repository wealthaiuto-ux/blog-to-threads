"""記事URLからHTMLを取得し、本文テキストとog:imageを抽出する。"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
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


# ボット保護の待機ページ。本文が空のまま生成に流すと、モデルが
# 「記事本文をご提供ください」と書いた文章を投稿案として保存してしまう。
CHALLENGE_TITLES = ("one moment", "just a moment", "attention required",
                    "checking your browser", "アクセスが制限")
MIN_BODY_LEN = 400


def fetch(url: str, retries: int = 2) -> dict:
    """記事本文を取り出す。取れなければ SystemExit（呼び元がその記事をスキップする）。

    短時間に連続アクセスするとボット保護の待機ページが返るので、
    その場合は少し待って取り直す。中身が薄いまま返さないこと。
    """
    last = ""
    for attempt in range(1, retries + 2):
        req = Request(url, headers={"User-Agent": "blog-to-threads/1.0"})
        with urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        p = _Extractor()
        p.feed(html)
        body = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
        title = p.title or ""

        blocked = any(k in title.lower() for k in CHALLENGE_TITLES)
        if not blocked and len(body) >= MIN_BODY_LEN:
            return {
                "url": url,
                "title": title,
                "body": body[:4000],
                "og_image": p.og_image,
            }

        last = (f"ボット保護の待機ページ（title={title[:40]}）" if blocked
                else f"本文が短すぎる（{len(body)}字 < {MIN_BODY_LEN}字）")
        if attempt <= retries:
            print(f"[retry] {url} — {last}", file=sys.stderr)
            time.sleep(20 * attempt)

    raise SystemExit(f"記事本文を取得できなかった: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    args = ap.parse_args()
    print(json.dumps(fetch(args.url), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
