"""Notion DB「Threadsドラフト (blog-to-threads)」とのやり取り。

- save_draft(article, posts, image_url): 新規ページを status=draft で作成
- fetch_oldest_approved(): status=approved の最古ページを1件取得
- mark_posted(page_id, post_url): status=posted に更新して投稿URL/日時を書き込み

環境変数:
  NOTION_API_KEY        - Notion integration token
  NOTION_DATABASE_ID    - 対象DB（既定値あり）
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

DEFAULT_DB_ID = "0f4390afe11b45049c0f3639d9004ab0"
API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def _headers() -> dict:
    key = os.environ.get("NOTION_API_KEY")
    if not key:
        raise SystemExit("NOTION_API_KEY が未設定")
    return {
        "Authorization": f"Bearer {key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = Request(f"{API_BASE}{path}", data=data, method=method, headers=_headers())
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        raise SystemExit(f"Notion API {method} {path} -> {e.code}: {e.read().decode('utf-8', errors='replace')}")


def _db_id() -> str:
    return os.environ.get("NOTION_DATABASE_ID", DEFAULT_DB_ID)


def _rt(text: str) -> list[dict]:
    # Notionのrich_textは2000字制限。Threadsは500字なので余裕。
    return [{"type": "text", "text": {"content": text}}]


def save_draft(article: dict, posts: list[str], image_url: str | None) -> str:
    """draft を1ページ作成、ページIDを返す。"""
    props: dict = {
        "タイトル": {"title": _rt(article.get("title", "(無題)"))},
        "ステータス": {"select": {"name": "draft"}},
        "記事URL": {"url": article.get("url")},
        "投稿1": {"rich_text": _rt(posts[0])},
        "投稿2": {"rich_text": _rt(posts[1])},
    }
    if len(posts) >= 3:
        props["投稿3"] = {"rich_text": _rt(posts[2])}
    if image_url:
        props["画像URL"] = {"url": image_url}

    res = _request("POST", "/pages", {
        "parent": {"database_id": _db_id()},
        "properties": props,
    })
    return res["id"]


def fetch_oldest_approved() -> dict | None:
    """status=approved の最古ページ1件を辞書で返す。無ければNone。"""
    res = _request("POST", f"/databases/{_db_id()}/query", {
        "filter": {"property": "ステータス", "select": {"equals": "approved"}},
        "sorts": [{"property": "生成日時", "direction": "ascending"}],
        "page_size": 1,
    })
    if not res.get("results"):
        return None

    page = res["results"][0]
    props = page["properties"]

    def _read_rt(name: str) -> str:
        items = props.get(name, {}).get("rich_text") or []
        return "".join(i.get("plain_text", "") for i in items)

    posts = [_read_rt("投稿1"), _read_rt("投稿2")]
    p3 = _read_rt("投稿3")
    if p3.strip():
        posts.append(p3)

    return {
        "page_id": page["id"],
        "article_url": props.get("記事URL", {}).get("url"),
        "title": "".join(t.get("plain_text", "") for t in props.get("タイトル", {}).get("title", [])),
        "posts": posts,
        "image_url": props.get("画像URL", {}).get("url"),
    }


def mark_posted(page_id: str, thread_root_id: str, post_url: str | None = None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    props: dict = {
        "ステータス": {"select": {"name": "posted"}},
        "投稿日時": {"date": {"start": now}},
    }
    if post_url:
        props["投稿URL"] = {"url": post_url}
    _request("PATCH", f"/pages/{page_id}", {"properties": props})


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch-approved":
        result = fetch_oldest_approved()
        print(json.dumps(result, ensure_ascii=False) if result else "")
    elif cmd == "mark-posted":
        page_id = sys.argv[2]
        thread_id = sys.argv[3]
        url = sys.argv[4] if len(sys.argv) > 4 else None
        mark_posted(page_id, thread_id, url)
        print("ok")
    else:
        print("usage: notion_draft.py [fetch-approved | mark-posted <page_id> <thread_id> [url]]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
