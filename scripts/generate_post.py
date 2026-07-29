"""単発のThreads投稿を1本生成する（playbook が決めた型・テーマ・記事に従う）。

旧ツリー版（削除済み）との違い:
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

# 生成物の「下書きメモ」と「投稿本文」を分ける印。
# メモを書かせた方が投稿の質は上がるが、そのまま出力に混ざると字数を食って全部落ちる。
POST_MARKER = "===投稿==="


def extract_post(raw: str) -> str:
    """モデルの出力から投稿本文だけを取り出す。

    印が無い場合は全文を本文とみなす（従来どおり）。
    印が複数あるときは最後のものを使う。
    """
    if POST_MARKER in raw:
        raw = raw.rsplit(POST_MARKER, 1)[1]
    return raw.strip()

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
    (r"(僕|俺)(は|が|の|も|に)", "一人称が「私」でない"),
    # --- AIっぽさ（2026-07-29追加）---
    # 正体は文体ではなく構造。論点を並べて綺麗に締める書き方が「説明文」に見える。
    (r"(ポイントは|まとめると|結論から言うと|要するに)", "説明文の型（論点整理）"),
    (r"(参考になれば|お役に立て)", "ブログの締め文句"),
    (r"ではないでしょうか", "断定を避ける説明口調"),
    (r"(という点|といった点)", "説明口調（〜という点）"),
    (r"皆さん(は|も)", "読者への呼びかけが説明会っぽい"),
    (r"^(今回は|本日は|この記事では)", "前置きから入っている"),
    (r"\*\*", "下書きメモの見出し記号（**）が残っている"),
    (r"(予想と実際|拾う内容|拾うもの)", "下書きメモが本文に混ざっている"),
]

# 絵文字（ざっくり範囲。厳密さより「多すぎ」を止められればよい）
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿]"
)


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

    # 箇条書きを並べた瞬間に「説明資料」になる。主題は1つに絞らせる。
    bullets = len(re.findall(r"^\s*[・･]", text, re.MULTILINE))
    if bullets >= 3:
        problems.append(f"箇条書きが3個以上（{bullets}個）。主題を1つに絞る")

    if len(EMOJI_RE.findall(text)) > 2:
        problems.append("絵文字が3個以上")

    # おすすめ欄では冒頭2行しか読まれない。1行目が長い/説明だと、そこで流される。
    first = text.strip().split("\n", 1)[0]
    if re.match(r"^.{0,12}(とは|について)[、。]?$", first):
        problems.append("1行目が説明の見出しになっている")
    if len(first) > 60:
        problems.append(f"1行目が長い（{len(first)}字）。冒頭2行で刺さらないと流れる")

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

**軸の徹底（最重要）**: テーマが家電でも育児でもお金でも、読者が「同じ人が書いている」と
感じられることが最優先。軸は1つだけ ── 実際に買って、数字を出して、向いてない人も言う人。
型が「実測値型」でも「1コマ型」でも、この軸からブレないこと。
軸をブラすくらいなら、型らしさより軸を優先する。

**文体（口語・2026-07-27確定）**: 硬い書き言葉にしない。友達に話すくらいの温度感。
- 文末は「〜みたい」「〜って」「〜だった気がする」「〜かなと思ってる」のように断定を少し崩す
- 「なんか」「けっこう」「だいぶ」「すごい」のような口語のフィラーを自然に混ぜてよい
- 「〜だ。」「〜である。」のような言い切りの書き言葉は避ける
- 例（この温度感を目安にする。丸写しはしない）:
  「ラムダッシュの替刃って公式だと外刃1年が目安らしいんだけど、うち2年放置してる。
  やってることは週1で水洗いしてオイル差すくらい。
  それでも剃り味落ちたなーって思ったことがまだない。」

**フォローされる投稿の条件（2026-07-29追加・最重要）**

このアカウントの目的は送客ではなく、フォローされること。
「役に立った」で終わる投稿はフォローされない。読んだ時点で用が済むから。
フォローは「この人の次も読みたい」と思われたときにだけ起きる。そのために必ず入れる:

1. **発見を1つ**: 自分の予想と違ったこと。「思ったより安かった」「逆に増えた」
   「聞いてた話と違った」。数字そのものではなく、数字を見て自分が何を感じたかを書く
2. **感情の起点**: 結果ではなく、買う前の迷い・後悔・怒り・ホッとした瞬間から入る。
   人は結論ではなく、揺れている姿に人格を感じてフォローする
3. **閉じきらない**: 「まだ結論は出てない」「今これ試してる」のように、続きがある終わり方でよい。
   綺麗にまとめて締めない

**書き方の構造（AIっぽさはここで決まる）**

AIっぽさの正体は語尾ではなく構造。論点を3つ並べて綺麗に締めるのが「説明文」に見える原因。
- **主題は1つだけ**。2つ書きたくなったら1つ捨てる
- **冒頭2行（40字前後）で勝負が決まる**。おすすめ欄では続きを開かれずに流れるため、
  1行目に説明・前置き・背景を書かない。感情か、予想が外れた一点をいきなり置く
- 箇条書きを並べない（使っても2個まで）
- 「ポイントは」「まとめると」「結論から言うと」「参考になれば」は書かない
- 綺麗に終わらせようとしない。言い切らずに終わってよい

出力形式:
- 投稿本文だけを出力する。前置き・解説・見出し・鍵括弧での囲みは一切書かない
- 200字前後（最大450字）
- 改行で読みやすく区切る
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
記事全体を紹介しようとしないこと。1つのシーン、1つの数字、1つの後悔に絞る。

出力は必ず次の形式にしてください:

1. まず、記事から次の2つを拾って書く（下書きメモ。投稿には使われない）
   - 自分の予想と実際がズレた一点
   - そのとき何を感じたか（迷い・後悔・安心・怒り）
2. 次に、{POST_MARKER} だけの行を置く
3. その下に、投稿本文だけを書く。メモの文言や見出し記号を持ち込まないこと

{POST_MARKER} から下が、そのままThreadsに投稿されます。
商品の説明から入らず、上で拾った感情かズレから書き出してください。"""

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


def build_idea_prompt(idea: str, detail: str = "") -> tuple[str, str]:
    """ネタ帳の一言から投稿を書くためのプロンプト。

    記事と違い、ネタは本人が出した「これ話したい」。切り口を機械で押し付けず、
    ネタが持っている温度をそのまま活かす。ここで事実を足さないことが最重要
    （ネタに書いていない数字やエピソードを創作すると、本人の体験を捏造することになる）。
    """
    system = f"""{load_constitution()}

---

あなたは石井雄都（ちゃんこ）本人として、Threadsの投稿を1本書く。
上のルールは絶対に守ること。

これは本人が「これを話したい」と放り込んだネタです。
ネタに込められた温度・言いたいことを、本人の言葉で1本の投稿に仕上げてください。

**軸の徹底（最重要）**: テーマが家電でも育児でもお金でも、読者が「同じ人が書いている」と
感じられることが最優先。軸は1つだけ ── 実際に買って、数字を出して、向いてない人も言う人。
ネタの温度を活かしつつ、この軸からブレないこと。

**文体（口語・2026-07-27確定）**: 硬い書き言葉にしない。友達に話すくらいの温度感。
- 文末は「〜みたい」「〜って」「〜だった気がする」「〜かなと思ってる」のように断定を少し崩す
- 「なんか」「けっこう」「だいぶ」「すごい」のような口語のフィラーを自然に混ぜてよい
- 「〜だ。」「〜である。」のような言い切りの書き言葉は避ける

**書き方の構造（2026-07-29追加）**: AIっぽさの正体は語尾ではなく構造。
- **主題は1つだけ**。論点を並べない。箇条書きは使っても2個まで
- 1行目に説明を書かない。感情か、予想が外れた一点から入る
- 「ポイントは」「まとめると」「結論から言うと」「参考になれば」は書かない
- 綺麗にまとめて締めない。言い切らずに終わってよい

出力形式:
- 投稿本文だけを出力する。前置き・解説・鍵括弧での囲みは書かない
- 200字前後（最大450字）
- 改行で読みやすく区切る
- URLは書かない。ハッシュタグは付けない

**最重要**: ネタに書かれていない事実（数字・エピソード・家族の発言）を足さないこと。
ネタが短ければ短いまま、盛らずに仕上げる。膨らませるより、削って刺す。"""

    user = f"今回のネタ:\n{idea}"
    if detail:
        user += f"\n\n補足:\n{detail}"
    user += "\n\nこのネタを、Threadsの投稿1本にしてください。"
    return system, user


def generate_from_idea(idea: str, detail: str = "", max_retry: int = 3) -> dict:
    """ネタ帳の一言から投稿を生成する。戻り値は generate() と同じ形。"""
    system, user = build_idea_prompt(idea, detail)
    last_text, last_problems = "", ["生成できなかった"]
    for attempt in range(1, max_retry + 1):
        text = extract_post(call_claude(system, user))
        problems = check(text, "")  # ネタにはタイトル丸写しの概念がないので空で渡す
        if not problems:
            return {"text": text, "status": "draft", "problems": [], "attempts": attempt}
        print(f"[filter] {attempt}回目 不合格: {' / '.join(problems)}", file=sys.stderr)
        last_text, last_problems = text, problems
        user += f"\n\n前回の出力は次の理由で却下されました。直してください: {' / '.join(problems)}"
    return {"text": last_text, "status": "needs_fix", "problems": last_problems, "attempts": max_retry}


# リプ（リンク側）で使ってはいけない表現。
# 「押させる」書き方をした瞬間に宣伝アカウントに見えるので、機械で落とす。
REPLY_NG_PATTERNS: list[tuple[str, str]] = [
    (r"詳しく", "誘導文句（詳しく）"),
    (r"こちら", "誘導文句（こちら）"),
    (r"ぜひ", "誘導文句（ぜひ）"),
    (r"チェック", "誘導文句（チェック）"),
    (r"読んで(みて|ください)", "誘導文句（読んでみて）"),
    (r"(気になる|興味がある)方", "誘導文句（気になる方）"),
    (r"(僕|俺)(は|が|の|も|に)", "一人称が「私」でない"),
]

REPLY_MAX_LEN = 70


def check_reply(text: str) -> list[str]:
    """リンク用リプの自動検査。URLは含まれていて当然なので、そこは見ない。"""
    problems = [reason for pattern, reason in REPLY_NG_PATTERNS if re.search(pattern, text)]
    body = re.sub(r"https?://\S+", "", text).strip()
    if len(body) > REPLY_MAX_LEN:
        problems.append(f"リプが長すぎる（{len(body)}字 > {REPLY_MAX_LEN}字）")
    if len(body) < 8:
        problems.append("リプが短すぎる")
    return problems


def generate_link_reply(article: dict, root_text: str, max_retry: int = 3) -> str | None:
    """ルート投稿にぶら下げる「リンクを置いておくだけ」のリプを書く。

    狙いは誘導ではなく設置。リンク先に何があるか（見積書・内訳・手順など）を
    名指しするだけにして、「詳しくはこちら」型の宣伝文にしない。
    検査を通らなければ None を返し、その投稿はリンク無しで出す（無理に付けない）。
    """
    system = """あなたは石井雄都（ちゃんこ）本人として、自分のThreads投稿にぶら下げる
リプライを1行だけ書く。

これは宣伝ではない。「さっきの話の元データはここに置いてある」と伝えるだけの1行。

守ること:
- リンク先に実際にある具体物を名指しする（例: 見積書、内訳、手順、比較表、月々の金額）
- 「詳しくは」「こちら」「ぜひ」「チェック」「気になる方は」は絶対に使わない
- 記事に書いていないものを名指ししない
- 一人称は「私」。口語で、友達に補足するくらいの温度感
- 30字前後。長くても70字
- URLは書かない（こちらで後から付ける）
- 本文だけを出力する。前置き・解説・鍵括弧は書かない

良い例:
「見積書そのまま貼ってあるので、これから頼む人は自分の家と比べてみてください」
「工事の手順は長くなるから別で書いてます」
「他社と比べた表もそっちに置いてます」

悪い例（絶対に書かない）:
「詳しくはブログで！」「気になる方はこちらから」「ぜひ読んでみてください」"""

    user = f"""さっき投稿した本文:
{root_text}

リンク先の記事:
タイトル: {article['title']}

本文:
{article['body'][:2000]}

この記事の中にあって、上の投稿では触れていない「具体物」を1つ選び、
それが置いてあることを伝える1行を書いてください。"""

    for attempt in range(1, max_retry + 1):
        text = call_claude(system, user)
        problems = check_reply(text)
        if not problems:
            return f"{text}\n{article['url']}"
        print(f"[filter] リプ{attempt}回目 不合格: {' / '.join(problems)}", file=sys.stderr)
        user += f"\n\n前回の出力は次の理由で却下されました。直してください: {' / '.join(problems)}"

    print("[warn] リプが検査を通らなかったのでリンク無しで出す", file=sys.stderr)
    return None


def generate(decision: dict, article: dict, max_retry: int = 3) -> dict:
    """検査を通るまで最大 max_retry 回生成する。

    戻り値: {"text": str, "status": "draft"|"needs_fix", "problems": [...], "attempts": int}
    """
    system, user = build_prompt(decision, article)
    last_text, last_problems = "", ["生成できなかった"]

    for attempt in range(1, max_retry + 1):
        text = extract_post(call_claude(system, user))
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
