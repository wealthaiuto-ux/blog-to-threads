# Threads Graph API メモ

公式: https://developers.facebook.com/docs/threads/

## エンドポイント

- ベースURL: `https://graph.threads.net/v1.0`
- 認証: `access_token` クエリパラメータ（long-lived token推奨：60日）

## ツリー投稿の手順

各投稿は **container作成 → 30秒待機 → publish** の2ステップ。

### 1. テキスト投稿（親）

```
POST /{user-id}/threads
  access_token=...
  media_type=TEXT
  text=本文
```
→ レスポンス `{"id": "<container_id>"}`

### 2. 画像付き投稿

```
POST /{user-id}/threads
  access_token=...
  media_type=IMAGE
  image_url=https://...   ← 公開URLが必要（ローカルファイル不可）
  text=本文
```

### 3. 公開

```
POST /{user-id}/threads_publish
  access_token=...
  creation_id=<container_id>
```
→ レスポンス `{"id": "<published_post_id>"}`

**重要**: container作成から `media_publish` まで **最低30秒空ける** ことを公式が推奨。
スクリプトでは `time.sleep(30)` で対応している。

### 4. 返信（ツリーの2投稿目以降）

```
POST /{user-id}/threads
  access_token=...
  media_type=TEXT
  text=本文
  reply_to_id=<前の published_post_id>
```
→ container作成 → publish の流れは同じ。

## 制約

- 1投稿: 最大500字
- 画像: JPEG/PNG、最大8MB、公開URL必須
- レート: 24時間で250投稿まで
- リンクは1投稿に1つまで自動展開される

## 画像の公開URL問題

ローカル生成した画像をAPIに渡すには、どこかにアップロードして公開URLを得る必要がある。
案：

1. **og:image を使う**（実装済み）: ブログ記事のアイキャッチを流用
2. GitHub Pages: リポジトリの `docs/` に置いて raw URL を取る
3. Cloudflare R2 / S3 / imgbb: APIで毎回アップロード

現状は (1) のみ実装。Nano Banana生成画像を使いたい場合は (2)(3) を実装する必要がある。

## トークン更新

long-lived token は 60日で失効する。失効前に refresh する：

```
GET /refresh_access_token?grant_type=th_refresh_token&access_token=<current>
```

GitHub Actions の cron で月1回 refresh するワークフローを追加するのが安全。
