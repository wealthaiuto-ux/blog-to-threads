"""単発のThreads投稿を1本生成する（playbook が決めた型・テーマ・記事に従う）。

旧 generate_tree.py との違い:
  - ツリーではなく単発。ルート投稿だけで完結して読めること
  - 型（6種）ごとにプロンプトを変える
  - ルート投稿にURLを入れない（配信が絞られるため）
  - 憲法 CLAUDE.md を毎回読み込ませる

生成後は必ず check() を通す。落ちたらリトライし、通らなければ needs_fix として保存する。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CONSTITUTION = ROOT / "CLAUDE.md"
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

MAX_LEN = 450
MIN_LEN = 60

# 生成物を人に見せる前に自動で弾く条件。
# 旧システムは「【案A・体験談型】」のような内部ラベルが付いたまま公開される事故を起こした。
NG_PATTERNS: list[tuple[str, str]] = [
    (r"【案", "内部ラベル（【案）が残っている"),
    (r"【パターン", "内部ラベル（【パターン）が残っている"),
    (r"\|\s*ちゃんこ", "記事タイトルのサイト名部分が残っている"),
    (r"https?://", "ルート投稿にURLが入っている"),
    (r"いかがでしたか", "定型句（いかがでしたか）"),
    (r"について解説します", "定型句（について解説します）"),
    (r"必ず.{0,6}(できます|なります)", "誇大な断定"),
    (r"(誰でも|確実に|100%|絶対に)", "誇大な断定"),
    (r"(今だけ|期間限定|損します|読まないと損)", "煽り表現"),
]


def load_constitution() -> str:
    return CONSTITUTION.read_text(encoding="utf-8") if CONSTITUTION.exists() else ""


def check(text: str, article_title: str) -> list[str]:
    """投稿案の自動検査。問題があれば理由の一覧を返す（空なら合格）。"""
    problems = []
    for pattern, reason in NG_PATTERNS:
        if re.search(pattern, text):
            problems.append(reason)

    if len(text) > MAX_LEN:
        problems.append(f"長すぎる（{len(text)}字 > {MAX_LEN}字）")
    if len(text) < MIN_LEN:
        problems.append(f"短すぎる（{len(text)}字 < {MIN_LEN}字）")

    if text.count("#") > 1:
        problems.append("ハッシュタグが2個以上")

    # 記事タイトルの丸写し（先頭30字が本文に含まれる）
    head = re.sub(r"[｜|].*$", "", article_title).strip()[:30]
    if len(head) >= 12 and head in text:
        problems.append("記事タイトルの丸写し")

    return problems


def build_prompt(decision: dict, article: dict) -> tuple[str, str]:
    system = f"""{load_constitution()}

---

あなたは石井雄都（ちゃんこ）本人として、Threadsの投稿を1本書く。
上のルールは絶対に守ること。

出力形式:
- 投稿本文だけを出力する。前置き・解説・見出し・鍵括弧での囲みは一切書かない
- 200字前後（最大450字）
- 改行で読みやすく区切る。箇条書きは「・」を使ってよい
- 単発の投稿として、これだけ読んで意味が通ること
- **URLは絶対に書かない**（リンクは後からリプに付ける運用のため）
- ハッシュタグは付けない
- 記事タイトルをそのまま書き写さない。自分の言葉で語り直す
"""

    user = f"""今回の型: 【{decision['post_type']}】
{decision['type_desc']}

この型のイメージ（文体をコピーするのではなく、切り口の参考にする）:
{decision['type_example']}

---

元になる自分の記事（ここから実体験・数字・固有名詞を拾う。要約はしない）:

タイトル: {article['title']}

本文:
{article['body'][:3000]}

---

この記事の中から「型に合う一点」だけを選んで、Threadsの投稿を1本書いてください。
記事全体を紹介しようとしないこと。1つのシーン、1つの数字、1つの後悔に絞る。"""

    return system, user


def call_claude(system: str, user: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY が未設定")

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1200,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")

    req = Request(API_URL, data=body, method="POST", headers={
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        with urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read())
    except HTTPError as e:
        raise SystemExit(f"Anthropic API error {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}")

    return "".join(b.get("text", "") for b in res.get("content", [])).strip()


def generate(decision: dict, article: dict, max_retry: int = 3) -> dict:
    """検査を通るまで最大 max_retry 回生成する。

    戻り値: {"text": str, "status": "draft"|"needs_fix", "problems": [...], "attempts": int}
    """
    system, user = build_prompt(decision, article)
    last_text, last_problems = "", ["生成できなかった"]

    for attempt in range(1, max_retry + 1):
        text = call_claude(system, user)
        problems = check(text, article["title"])
        if not problems:
            return {"text": text, "status": "draft", "problems": [], "attempts": attempt}
        print(f"[filter] {attempt}回目 不合格: {' / '.join(problems)}", file=sys.stderr)
        last_text, last_problems = text, problems
        # 落ちた理由を次の指示に足して直させる
        user += f"\n\n前回の出力は次の理由で却下されました。直して書き直してください: {' / '.join(problems)}"

    return {"text": last_text, "status": "needs_fix", "problems": last_problems, "attempts": max_retry}


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fetch_article  # type: ignore
    import playbook  # type: ignore

    ap = argparse.ArgumentParser()
    ap.add_argument("--type", dest="force_type")
    ap.add_argument("--theme", dest="force_theme")
    ap.add_argument("--article-url")
    args = ap.parse_args()

    decision = playbook.decide(force_type=args.force_type, force_theme=args.force_theme)
    meta = decision["article"]
    if args.article_url:
        meta = {"url": args.article_url, "title": ""}

    article = fetch_article.fetch(meta["url"])
    result = generate(decision, article)

    print(json.dumps({
        "post_type": decision["post_type"],
        "theme": decision["theme"],
        "article_url": meta["url"],
        "article_title": article["title"],
        **result,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
