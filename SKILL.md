---
name: blog-to-threads
description: chanko06.com（ちゃんこライフハックブログ）の記事をRSSクロールして、Threadsに2〜3投稿のツリー形式で投稿するスキル。生成と投稿を2段階に分けてNotion DB「Threadsドラフト (blog-to-threads)」でレビュー可能にする。前夜に3本ドラフト生成→朝/昼/夜の前にNotionで承認（status=approved）→GitHub Actionsが朝7時/昼11時/夜20時 JSTにapprovedの最古を投稿。「Threadsに投稿して」「ブログからスレッズ作って」「Threadsドラフト作って」「今日のThreads自動投稿」「ブログのツリー投稿」と言われたら必ず起動。手動でも `python scripts/run_generate.py` / `python scripts/run_post.py` で動かせる。
---

# blog-to-threads

chanko06.com の記事を **Threadsツリー投稿** に展開してブログへの導線を作るスキル。
**生成 → Notionレビュー → 投稿** の2段階フローで、AI生成の事故を防ぐ。

## 全体像

```
[前夜 22:00 JST]  generate-drafts.yml
  RSS → 記事選定 → Claude APIでツリー生成 → Notionに status=draft で保存（3本）
        ↓
[ゆうと]  スマホ/PCでNotionレビュー
  OKなら status=approved に変更、ダメなら status=cancelled
        ↓
[朝7/昼11/夜20 JST]  post-thread.yml
  Notionから status=approved の最古ページ1件を取得
  → Threads APIでツリー投稿
  → status=posted に更新、投稿URLを書き込み
  approved=0件のときは何もせずスキップ（事故防止）
```

## Notion DB

- DB名: **Threadsドラフト (blog-to-threads)**
- URL: https://app.notion.com/p/0f4390afe11b45049c0f3639d9004ab0
- DB ID: `0f4390afe11b45049c0f3639d9004ab0`

スキーマ:
| プロパティ | 型 | 用途 |
|---|---|---|
| タイトル | Title | 記事タイトル |
| ステータス | Select | `draft` / `approved` / `posted` / `cancelled` |
| 記事URL | URL | 元のブログ記事 |
| 投稿1 / 投稿2 / 投稿3 | Rich text | ツリー本文（3は2本構成時 空） |
| 画像URL | URL | 1投稿目に添付 |
| 投稿日時 | Date | 投稿時に書き込み |
| 投稿URL | URL | 投稿時に書き込み |
| 生成日時 | Created time | 自動 |

## 使い方

### 普段の運用（自動）

1. **前夜（22:00 JST）**: GitHub Actions `generate-drafts.yml` がドラフト3本を生成しNotionへ保存
2. **朝起きてスマホで**: NotionアプリでDBを開き、各ページの本文を確認 → 良いものは `ステータス` を `approved` に変更
3. **朝7/昼11/夜20 JST**: GitHub Actions `post-thread.yml` がapprovedの最古を1件取って投稿
4. approvedが0件なら投稿はスキップされる（壊れない）

### 手動で動かしたいとき

```bash
cd ~/.claude/skills/blog-to-threads
export ANTHROPIC_API_KEY=...
export NOTION_API_KEY=...

# ドラフト生成（Notionに保存される）
python3 scripts/run_generate.py --count 3

# 特定記事から生成したい
python3 scripts/run_generate.py --article-url https://chanko06.com/xxx/

# 投稿実行（approvedの最古を投稿）
export THREADS_ACCESS_TOKEN=...
export THREADS_USER_ID=...
python3 scripts/run_post.py --dry-run   # 内容確認
python3 scripts/run_post.py             # 実投稿
```

### ユーザーから「Threadsに投稿して」と言われたとき

1. 「ドラフトを作りますか？それともNotionで承認済みのを投稿しますか？」と確認
2. ドラフト生成なら `run_generate.py` → Notionリンクを返す
3. 投稿なら `run_post.py --dry-run` で内容確認 → OKなら本実行

## ツリー投稿の設計思想

**目的**: ブログPV増加。最終投稿に必ずブログURLのCTAを入れる。

- **2〜3投稿の可変**: 記事の濃度で Claude が判断
- **1投稿目**: 読者の悩み/興味を刺すフック（200字前後）
- **2投稿目**: 記事の核心。固有名詞・数字を最低1つ
- **3投稿目（あれば）**: 「👇 続きはこちら」+ URL + ハッシュタグ2〜3個
- **2投稿構成のとき**: 2投稿目の末尾にURL

機械っぽい定型句（「いかがでしたか」「まとめ」「〜について解説します」）は禁止。

## ネタ選定ロジック

`scripts/pick_article.py`:
- 70%: 直近10件の新着からランダム
- 30%: それ以降の過去記事からランダム
- 過去7日以内に投稿した記事は除外（`posted_log.json` 参照）

## 画像差し込み

**50%確率** で1投稿目に **Nano Banana 生成画像** を添付（主人公キャラ固定）。
仕組み：

1. Claudeが `image_prompt`（英語1文）を生成
2. `maybe_image.py` が Nano Banana (gemini-3.1-flash-image-preview) で画像生成
3. `data/generated/thread_xxx.png` に保存
4. Actions が repo にコミット → `raw.githubusercontent.com/.../data/generated/xxx.png` が公開URLになる
5. このURLを Notion DBの `画像URL` に保存、Threads投稿時に1投稿目へ添付

主人公リファレンスは `assets/character.jpg` に同梱（リポジトリのみ）。
画像生成コストは約$0.04/枚 × 約45枚/月 = **約$1.8/月**。

## ファイル構成

```
scripts/
  crawl_blog.py         RSS取得 → data/articles_cache.json
  pick_article.py       記事選定（新着+過去ランダム）
  fetch_article.py      記事本文 + og:image 抽出
  generate_tree.py      Claude APIでツリー本文生成
  maybe_image.py        Nano Banana画像生成（将来用）
  post_threads.py       Threads API でツリー投稿
  notion_draft.py       Notion DBへ保存/取得/更新
  run_generate.py       生成ジョブのエントリポイント
  run_post.py           投稿ジョブのエントリポイント

references/
  threads-api.md        Threads Graph API 仕様メモ

data/
  articles_cache.json   RSSキャッシュ
  posted_log.json       投稿履歴

.github/workflows/
  generate-drafts.yml   前夜22時 ドラフト3本生成
  post-thread.yml       朝7/昼11/夜20 approved を投稿
```

## GitHub Secrets

| Secret | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | ツリー本文生成 |
| `NOTION_API_KEY` | Notion DB読み書き |
| `NOTION_DATABASE_ID` | 上記DBのID（省略時は既定値） |
| `THREADS_ACCESS_TOKEN` | Threads投稿 |
| `THREADS_USER_ID` | Threadsユーザー ID |
| `GOOGLE_AI_API_KEY` | Nano Banana画像生成（50%確率で使用） |

## エラーハンドリング方針

- approved=0件 → 静かにスキップ（壊れない）
- RSS失敗 → リトライ3回 → 諦めて翌回
- Claude API失敗 → 1案分だけ落として続行
- Threads API失敗 → Notionステータスは draft/approved のまま → 翌回再試行
- 画像生成失敗 → 画像なしで投稿継続

「投稿できなかった日があってもいい」が「壊れて気づかない」のはダメ。

## 関連スキル

- `engagement-tracker` — 投稿後の反応を記録・分析（投稿URLを連携）
- `daily-post` — 単発Threadsを手動で作る
- `blog-health-checker` — ブログ全体のSEO分析
