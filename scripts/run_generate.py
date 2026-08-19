"""生成ジョブ（v3）：playbook が決めた型・テーマ・記事で単発投稿を1本作り、Notionに保存する。

v2 までとの違い:
  - ツリー生成 → 単発生成（generate_post.py）
  - 記事選択が「新着70%のランダム」→ playbook.py（未使用優先・型との組み合わせ管理）
  - 生成物を機械フィルタに通し、通らなければ needs_fix で保存して人には出さない
  - generated_log に post_type / theme を記録する（月次レビューの比較軸になる）

設計の全体像は REDESIGN.md、ルールは CLAUDE.md を参照。
旧ツリー版（generate_tree.py / maybe_image.py / run_once.py）は 2026-07-29 に削除した。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import classify_articles  # type: ignore
import crawl_blog  # type: ignore
import fetch_article  # type: ignore
import generate_post  # type: ignore
import notion_draft  # type: ignore
import playbook  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
GEN_LOG_PATH = ROOT / "data" / "generated_log.json"


def _append_gen_log(entry: dict) -> None:
    log = []
    if GEN_LOG_PATH.exists():
        try:
            log = json.loads(GEN_LOG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log = []
    log.append(entry)
    GEN_LOG_PATH.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate_with_gate(pb: dict, decision: dict, article: dict, n: int = 3) -> dict:
    """同じ記事で型を変えた案を n 本作り、検査を通ったものから1本選ぶ。

    2026-08-20追加。それまでは1案を作って検査に通った瞬間に採用していたため、
    「合格ラインぎりぎりの凡庸な案」がそのまま下書きになっていた。

    選び方は「最高得点」ではない。いま勝ち負けの実績データが無いので、点数は
    形式の整い方しか測れず、それで最適化すると毎回同じような投稿に収束する。
    そこで、検査を通った案のうち **直近に使っていない型を優先** する。
    最適化ではなく探索として使う。

    全部落ちたときは、いちばん問題の少ない案を needs_fix として返す（人が直す）。
    """
    max_retry = pb.get("rules", {}).get("max_filter_retry", 3)
    recent = playbook.recent_types(pb.get("rules", {}).get("type_cooldown_days", 3))

    # 候補の型: 最初の決定 + 在庫にある他の型（重みの高い順）。同じ記事で切り口だけ変える
    types = [decision["post_type"]]
    for t, v in sorted(pb["types"].items(), key=lambda x: -x[1].get("weight", 0)):
        if t not in types and len(types) < n:
            types.append(t)

    passed, fallback = [], None
    for t in types:
        d = dict(decision, post_type=t,
                 type_desc=pb["types"][t]["desc"],
                 type_example=pb["types"][t].get("example", ""))
        res = generate_post.generate(d, article, max_retry=max_retry)
        res["decision"] = d
        if res["status"] == "draft":
            passed.append(res)
        elif fallback is None or len(res["problems"]) < len(fallback["problems"]):
            fallback = res
        print(f"[候補] {t}: {res['status']}"
              f"{' / ' + ' / '.join(res['problems']) if res['problems'] else ''}", file=sys.stderr)

    if not passed:
        print(f"[gate] {len(types)}案すべて検査に落ちた。最も問題の少ない案を needs_fix で残す",
              file=sys.stderr)
        return fallback

    # 直近に使っていない型を優先（同点なら先に生成されたもの）
    fresh = [r for r in passed if r["decision"]["post_type"] not in recent]
    chosen = (fresh or passed)[0]
    print(f"[gate] {len(passed)}/{len(types)}案が合格 → [{chosen['decision']['post_type']}] を採用"
          f"{'（直近未使用の型）' if fresh else '（全て直近使用済みのため先頭を採用）'}", file=sys.stderr)
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1, help="生成本数（既定1。1日1本の運用）")
    ap.add_argument("--type", dest="force_type", help="型を指定（検証用）")
    ap.add_argument("--theme", dest="force_theme", help="テーマを指定（検証用）")
    ap.add_argument("--dry-run", action="store_true",
                    help="Notionに保存せず、生成結果を標準出力に出すだけ")
    args = ap.parse_args()

    pb = playbook.load_playbook()
    for w in playbook.check_inventory(pb):
        print(f"[warn] {w}", file=sys.stderr)

    # リンク付き投稿の割合（既定 1/3）。リンク無しを過半にしておくための上限。
    link_ratio = float(pb.get("rules", {}).get("link_reply_ratio", 0.34))

    # 記事キャッシュを更新し、分類ファイルに無い新着があれば知らせる。
    # ここはブログの生死に依存する処理なので、失敗しても生成は止めない。
    # ネタ帳からの生成も、ブログ在庫（article_themes.json / キャッシュ）からの生成も、
    # このクロールが成功していなくても動く（クロールは新着検知のためだけ）。
    try:
        items = crawl_blog.fetch_feed()
        crawl_blog.save_cache(items)

        # 新着はその場で分類して在庫に入れる（キーワード規則なのでAPIキーは要らない）。
        # 既存の分類は上書きしないので、手で直したテーマは守られる。
        res = classify_articles.sync()
        if res["added"]:
            print(f"[classify] 新着{len(res['added'])}本を在庫に追加", file=sys.stderr)
            for t in res["added"][:5]:
                print(f"        + {t[:50]}", file=sys.stderr)
        if res["unclassified"]:
            print(f"[warn] キーワードに当たらず在庫に入らない記事が{len(res['unclassified'])}本。"
                  f"classify_articles.py の RULES にキーワードを足してください", file=sys.stderr)
            for t in res["unclassified"][:5]:
                print(f"        - {t[:50]}", file=sys.stderr)
    except SystemExit as e:
        print(f"[warn] ブログのクロールに失敗（{e}）。"
              f"新着検知はスキップし、既存の在庫で生成を続けます", file=sys.stderr)

    # --- ネタ帳を優先する（ゆうとさんが放り込んだ「これ話したい」を先に消化）---
    # ネタが count 本に足りなければ、残りをブログ記事から作る（下支え）。
    neta_list = [] if args.dry_run else notion_draft.fetch_neta(limit=args.count)
    remaining = args.count
    for neta in neta_list:
        if remaining <= 0:
            break
        if not neta["idea"]:
            continue
        result = generate_post.generate_from_idea(
            neta["idea"], neta["detail"],
            max_retry=pb.get("rules", {}).get("max_filter_retry", 3))
        notion_draft.update_neta_to_draft(
            neta["page_id"], result["text"], status=result["status"], problems=result["problems"])
        _append_gen_log({
            "article_url": None,
            "article_title": None,
            "source": "neta",
            "idea": neta["idea"],
            "post_type": "ネタ",
            "theme": "ネタ",
            "status": result["status"],
            "attempts": result["attempts"],
            "notion_page_id": neta["page_id"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
        mark = "⚠ needs_fix" if result["status"] == "needs_fix" else "saved"
        print(f"[{mark}] [ネタ] {neta['idea'][:36]} -> page={neta['page_id']}")
        remaining -= 1

    if neta_list:
        print(f"[neta] ネタ帳から{len(neta_list)}本消化。残り{remaining}本をブログから生成", file=sys.stderr)
    elif not args.dry_run:
        print("[neta] ネタ帳が空。記事から生成する（記事は下支えで、ネタの方が強い）", file=sys.stderr)

    # ネタ帳が空のまま記事に落ちたことを、承認画面の注記で知らせる
    neta_empty = not neta_list and not args.dry_run

    used_urls: set[str] = set()
    for i in range(remaining):
        decision = playbook.decide(pb, force_type=args.force_type, force_theme=args.force_theme)
        meta = decision["article"]
        if meta["url"].rstrip("/") in used_urls:
            print(f"[skip] 同一バッチ内で重複（{meta['title'][:30]}）", file=sys.stderr)
            continue
        used_urls.add(meta["url"].rstrip("/"))

        try:
            article = fetch_article.fetch(meta["url"])
        except SystemExit as e:
            print(f"[skip] 記事本文の取得に失敗（{meta['url']}）: {e}", file=sys.stderr)
            continue
        # 3案作って検査を通ったものから選ぶ（品質ゲート）。
        # 採用案の型は最初の decision と変わりうるので、以降は decision を差し替えて扱う。
        result = generate_with_gate(pb, decision, article,
                                    n=pb.get("rules", {}).get("gate_candidates", 3))
        decision = result.get("decision", decision)

        # リンクを付けるのは一定割合だけ。毎回リンクが付いている状態は、
        # 内容に関係なく「ブログ誘導アカウント」として認識されるため。
        # 検査に落ちた投稿にはリプを付けない（人が直す前提のものにリンクを足しても意味がない）。
        reply_text = None
        if result["status"] == "draft" and random.random() < link_ratio:
            reply_text = generate_post.generate_link_reply(
                article, result["text"],
                max_retry=pb.get("rules", {}).get("max_filter_retry", 3))

        header = (f"[{decision['post_type']}] [{decision['theme']}]"
                  f"{' [link]' if reply_text else ''} {article['title'][:36]}")
        if args.dry_run:
            print(f"\n=== {header} ===")
            print(f"status: {result['status']}  attempts: {result['attempts']}")
            if result["problems"]:
                print(f"problems: {' / '.join(result['problems'])}")
            print("-" * 40)
            print(result["text"])
            if reply_text:
                print("--- リプ（リンク） ---")
                print(reply_text)
            print("-" * 40)
            continue

        page_id = notion_draft.save_single(
            article, result["text"],
            post_type=decision["post_type"],
            theme=decision["theme"],
            status=result["status"],
            problems=result["problems"],
            reply_text=reply_text,
            neta_empty=neta_empty,
        )
        _append_gen_log({
            "article_url": meta["url"],
            "article_title": article["title"],
            "post_type": decision["post_type"],
            "theme": decision["theme"],
            "has_link": bool(reply_text),
            "status": result["status"],
            "attempts": result["attempts"],
            "notion_page_id": page_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })
        mark = "⚠ needs_fix" if result["status"] == "needs_fix" else "saved"
        print(f"[{mark}] {header} -> page={page_id}")
        # 保存したものも本文をログに出す。Notionを開かずに中身を確認できるようにするため。
        print("-" * 40)
        print(result["text"])
        if reply_text:
            print("--- リプ（リンク） ---")
            print(reply_text)
        print("-" * 40)

    return 0


if __name__ == "__main__":
    sys.exit(main())
