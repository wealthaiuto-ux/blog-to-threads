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


_SCHEMA_CACHE: dict | None = None


def db_properties() -> dict:
    """DBのプロパティ定義を取得（1回だけ）。

    型・テーマ・却下理由などの新しい項目は、Notion側に無ければ黙って書き込まない。
    「プロパティを足さないと生成が全部落ちる」状態を避けるため。
    """
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is None:
        _SCHEMA_CACHE = _request("GET", f"/databases/{_db_id()}").get("properties", {})
    return _SCHEMA_CACHE


def save_single(article: dict, text: str, *, post_type: str, theme: str,
                status: str = "draft", problems: list[str] | None = None,
                image_url: str | None = None) -> str:
    """単発投稿を1ページ作成する（v3）。

    ツリーではなく単発なので本文は「投稿1」にだけ入れる。
    型・テーマは月次レビューの比較軸になるが、Notion側は表示用。
    実際の分析は data/generated_log.json と posted_log.json を使う。
    """
    schema = db_properties()
    props: dict = {
        "タイトル": {"title": _rt(article.get("title", "(無題)"))},
        "ステータス": {"select": {"name": status}},
        "記事URL": {"url": article.get("url")},
        "投稿1": {"rich_text": _rt(text)},
    }
    if image_url:
        props["画像URL"] = {"url": image_url}

    # Notion側に該当プロパティがある場合だけ書き込む
    optional = {
        "型": {"select": {"name": post_type}},
        "テーマ": {"select": {"name": theme}},
    }
    for name, value in optional.items():
        if name in schema:
            props[name] = value

    res = _request("POST", "/pages", {
        "parent": {"database_id": _db_id()},
        "properties": props,
    })
    page_id = res["id"]

    # 型・テーマ・自動検査の結果はページ本文にも残す。
    # プロパティが無い環境でも、承認する人が何の型かを見られるようにするため。
    note = f"型: {post_type} ／ テーマ: {theme}"
    if problems:
        note += f"\n⚠ 自動検査に通らなかった項目: {' / '.join(problems)}"
    _request("PATCH", f"/blocks/{page_id}/children", {"children": [{
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rt(note),
            "icon": {"type": "emoji", "emoji": "⚠" if problems else "🏷"},
        },
    }]})
    return page_id


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


def _page_to_draft(page: dict) -> dict:
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


def fetch_approved(limit: int = 5) -> list[dict]:
    """status=approved のページを古い順に最大limit件返す。

    重複スキップで次の候補に進めるよう、1件ではなく複数返す。
    """
    res = _request("POST", f"/databases/{_db_id()}/query", {
        "filter": {"property": "ステータス", "select": {"equals": "approved"}},
        "sorts": [{"property": "生成日時", "direction": "ascending"}],
        "page_size": limit,
    })
    return [_page_to_draft(p) for p in res.get("results", [])]


def fetch_oldest_approved() -> dict | None:
    """status=approved の最古ページ1件を辞書で返す。無ければNone。"""
    drafts = fetch_approved(limit=1)
    return drafts[0] if drafts else None


def fetch_neta(limit: int = 5) -> list[dict]:
    """ステータス=ネタ の行を古い順に返す。ゆうとさんがスマホから放り込んだネタ。

    タイトル欄にネタの一言を書く。詳細を足したければ投稿1に書いてもよい。
    生成側はこれを拾って、同じ行を下書きに変える（4-1の投函口）。
    """
    res = _request("POST", f"/databases/{_db_id()}/query", {
        "filter": {"property": "ステータス", "select": {"equals": "ネタ"}},
        "sorts": [{"property": "生成日時", "direction": "ascending"}],
        "page_size": limit,
    })
    out = []
    for p in res.get("results", []):
        props = p["properties"]
        idea = "".join(t.get("plain_text", "") for t in props.get("タイトル", {}).get("title", []))
        detail = "".join(t.get("plain_text", "") for t in props.get("投稿1", {}).get("rich_text", []))
        out.append({"page_id": p["id"], "idea": idea.strip(), "detail": detail.strip()})
    return out


def update_neta_to_draft(page_id: str, text: str, *, status: str = "draft",
                         post_type: str = "ネタ", problems: list[str] | None = None) -> None:
    """ネタの行を、生成済みの下書きに変える（同じ行を使い回す。重複を作らない）。

    タイトル（＝元のネタ）はそのまま残し、生成した本文を投稿1に入れる。
    ゆうとさんは「自分が出したネタが、こう文章になった」と見比べて承認できる。
    """
    _request("PATCH", f"/pages/{page_id}", {"properties": {
        "ステータス": {"select": {"name": status}},
        "投稿1": {"rich_text": _rt(text)},
    }})
    note = f"型: {post_type}（ネタ帳から生成）"
    if problems:
        note += f"\n⚠ 自動検査に通らなかった項目: {' / '.join(problems)}"
    _request("PATCH", f"/blocks/{page_id}/children", {"children": [{
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rt(note),
            "icon": {"type": "emoji", "emoji": "⚠" if problems else "💡"},
        },
    }]})


def mark_skipped(page_id: str, reason: str) -> None:
    """投稿せずに見送ったドラフトに印を付ける。

    approved のまま残すと次回も同じページを拾って詰まるので、必ずステータスを変える。
    本文（投稿1〜3）は後から中身を確認できるよう、そのまま残す。
    """
    _request("PATCH", f"/pages/{page_id}", {"properties": {
        "ステータス": {"select": {"name": "skipped"}},
    }})
    # 理由はページ本文にコメントとして残す（プロパティを潰さないため）
    _request("PATCH", f"/blocks/{page_id}/children", {"children": [{
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rt(f"⏭ 投稿を見送りました: {reason}"),
            "icon": {"type": "emoji", "emoji": "⏭"},
        },
    }]})


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
