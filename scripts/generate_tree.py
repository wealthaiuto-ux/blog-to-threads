"""Claude APIで2〜3投稿のThreadsツリー本文を生成する。

入力: 記事のtitle/body/url（fetch_article.pyの出力JSON）
出力: { "posts": ["1投稿目", "2投稿目", ...], "image_prompt": "..." }
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """あなたは石井雄都（ちゃんこ）のSNS運用担当者です。
chanko06.com（ライフハック・AI活用ブログ）の記事を、Threadsで伸びるツリー投稿に変換します。

絶対ルール:
- 出力はJSONのみ（前後の説明文なし）
- posts配列は2要素または3要素。記事の濃度で判断（薄い→2、濃い→3）
- 各投稿は200字前後。最大でも450字。
- 1投稿目は読者の悩み/興味を1文で刺すフック。絵文字は1〜2個まで。
- 最後の投稿に必ずブログURLを「👇 続きはこちら\\n<URL>」の形で入れる。
- ハッシュタグは最後の投稿に2〜3個まで（#ライフハック #AI活用 #時短 など）。
- 「いかがでしたか」「まとめ」のような定型句は禁止。
- 機械っぽい言い回し（「〜について解説します」）も禁止。
- 体験談・固有名詞・数字を最低1つは含める。
- image_promptは1投稿目に添える画像の英語プロンプト（記事テーマを表すシーン1文）。"""


def call_claude(article: dict) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY が未設定")

    user = f"""記事タイトル: {article['title']}
記事URL: {article['url']}
記事本文（抜粋）:
{article['body'][:3000]}

この記事をThreadsツリー投稿に変換してください。JSON形式で返してください。
{{
  "posts": ["1投稿目テキスト", "2投稿目テキスト", ...],
  "image_prompt": "英語の画像生成プロンプト1文"
}}"""

    payload = {
        "model": MODEL,
        "max_tokens": 2048,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user}],
    }
    req = Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        raise SystemExit(f"Claude API error: {e.code} {e.read().decode('utf-8', errors='replace')}")

    text = data["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        if text.startswith("json\n"):
            text = text[5:]
    return json.loads(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="記事JSONファイル。未指定ならstdin")
    args = ap.parse_args()

    if args.input:
        with open(args.input, encoding="utf-8") as f:
            article = json.load(f)
    else:
        article = json.loads(sys.stdin.read())

    result = call_claude(article)
    if not isinstance(result.get("posts"), list) or not (2 <= len(result["posts"]) <= 3):
        raise SystemExit(f"posts配列が不正: {result}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
