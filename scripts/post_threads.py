"""Threads Graph API でツリー投稿する。

入力JSON:
{
  "posts": ["1投稿目", "2投稿目", ...],
  "image_url": "https://...（任意。1投稿目にだけ添付）"
}

Threads API ツリー投稿の流れ:
1. 1投稿目: media_type=TEXT (or IMAGE) で container 作成 → publish → root_id
2. 2投稿目以降: media_type=TEXT + reply_to_id=<直前のpublished id> → publish

ref: https://developers.facebook.com/docs/threads/posts
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GRAPH = "https://graph.threads.net/v1.0"


def _post(path: str, params: dict) -> dict:
    url = f"{GRAPH}{path}"
    data = urlencode(params).encode("utf-8")
    req = Request(url, data=data, method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        raise SystemExit(f"Threads API error {e.code}: {e.read().decode('utf-8', errors='replace')}")


def _create_container(user_id: str, token: str, text: str, *, reply_to_id: str | None = None,
                      image_url: str | None = None) -> str:
    params = {"access_token": token, "text": text}
    if image_url:
        params["media_type"] = "IMAGE"
        params["image_url"] = image_url
    else:
        params["media_type"] = "TEXT"
    if reply_to_id:
        params["reply_to_id"] = reply_to_id
    res = _post(f"/{user_id}/threads", params)
    return res["id"]


def _publish(user_id: str, token: str, container_id: str) -> str:
    res = _post(f"/{user_id}/threads_publish", {
        "access_token": token,
        "creation_id": container_id,
    })
    return res["id"]


def post_tree(posts: list[str], image_url: str | None = None) -> dict:
    user_id = os.environ.get("THREADS_USER_ID")
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not (user_id and token):
        raise SystemExit("THREADS_USER_ID / THREADS_ACCESS_TOKEN が未設定")

    published_ids: list[str] = []
    reply_to = None
    for i, text in enumerate(posts):
        img = image_url if i == 0 else None
        container = _create_container(user_id, token, text, reply_to_id=reply_to, image_url=img)
        # 推奨: container作成後30秒待ってからpublish（Meta公式）
        time.sleep(30)
        pub_id = _publish(user_id, token, container)
        published_ids.append(pub_id)
        reply_to = pub_id

    return {"root_id": published_ids[0], "all_ids": published_ids}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="生成済みJSONファイル。未指定ならstdin")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.load(open(args.input, encoding="utf-8")) if args.input else json.loads(sys.stdin.read())
    posts = data["posts"]
    image_url = data.get("image_url")

    if args.dry_run:
        print("=== DRY RUN ===")
        for i, p in enumerate(posts, 1):
            print(f"\n--- post {i} ({len(p)}字) ---\n{p}")
        if image_url:
            print(f"\n[image] {image_url}")
        return 0

    result = post_tree(posts, image_url)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
