#!/usr/bin/env python3
"""Threads長期トークンを更新し、GitHub Secrets（と ~/.claude/.env）を書き換える。

Threadsの長期トークンは60日で失効する。切れると投稿ジョブが毎回401で落ちるだけで
アカウント側には何も出ないので、気づくのが遅れる。

通常は token-refresh.yml が毎週勝手に更新するのでこれを叩く必要はない。
使うのは次の2ケース:
  - GH_PAT を置いていない運用で、週次チェックが「あと何日」と警告してきたとき
  - Actions 側が何らかの理由で更新に失敗したとき

  python3 scripts/refresh_token.py

GitHubへの反映は `gh` の既存ログインを使うのでPATは不要。
※すでに失効済みのトークンはこのスクリプトでは復活できない（APIが拒否する）。
  その場合は developers.facebook.com から取り直して --set で流し込む:
  python3 scripts/refresh_token.py --set "<新しいトークン>"
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ENV_PATH = os.path.expanduser("~/.claude/.env")
KEY = "THREADS_ACCESS_TOKEN"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_env_token():
    if not os.path.exists(ENV_PATH):
        return None, []
    lines = open(ENV_PATH).read().splitlines()
    tok = next((l.split("=", 1)[1].strip()
                for l in lines if l.startswith(KEY + "=")), None)
    return tok, lines


def write_env_token(new, lines):
    """~/.claude/.env の該当行を差し替える。行が無ければ追記する。"""
    if not lines:
        return
    shutil.copy(ENV_PATH, ENV_PATH + ".bak")
    if any(l.startswith(KEY + "=") for l in lines):
        out = [re.sub(r"^" + KEY + r"=.*$", f"{KEY}={new}", l) for l in lines]
    else:
        out = lines + [f"{KEY}={new}"]
    open(ENV_PATH, "w").write("\n".join(out) + "\n")
    print(f"✅ {ENV_PATH} を更新（バックアップ: {ENV_PATH}.bak）")


def refresh(old):
    url = "https://graph.threads.net/v1.0/refresh_access_token?" + urllib.parse.urlencode(
        {"grant_type": "th_refresh_token", "access_token": old})
    with urllib.request.urlopen(url, timeout=30) as r:
        res = json.load(r)
    new = res.get("access_token")
    if not new:
        sys.exit(f"❌ 更新に失敗: {res}")
    print(f"✅ 新トークン取得（有効期限 約{int(res.get('expires_in', 0)) // 86400}日）")
    return new


def verify(token):
    """更新後のトークンが本当に chanko_lifehack で通るか確かめてから保存する。"""
    url = ("https://graph.threads.net/v1.0/me?fields=id,username&access_token="
           + urllib.parse.quote(token))
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            me = json.load(r)
    except urllib.error.HTTPError as e:
        msg = json.loads(e.read() or b"{}").get("error", {}).get("message", "")
        sys.exit(f"❌ このトークンでは認証できません: {msg or e}\n"
                 "→ developers.facebook.com で取り直して --set に渡してください")
    if me.get("username") != "chanko_lifehack":
        sys.exit(f"❌ アカウント不一致: {me.get('username')} （chanko_lifehack のはず）")
    print(f"✅ 疎通確認 OK（@{me['username']}）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", metavar="TOKEN",
                    help="失効後に手で取り直したトークンを流し込む（更新APIを叩かない）")
    args = ap.parse_args()

    old, lines = read_env_token()
    if args.set:
        new = args.set.strip()
    else:
        if not old:
            sys.exit(f"❌ {ENV_PATH} に {KEY} がない。--set で新しいトークンを渡してください")
        new = refresh(old)

    verify(new)
    write_env_token(new, lines)

    try:
        subprocess.run(["gh", "secret", "set", KEY, "--body", new],
                       cwd=REPO_DIR, check=True)
        print("✅ GitHub Secrets も更新")
    except Exception as e:
        print(f"⚠️ GitHub Secretsの更新に失敗: {e}\n   手動: gh secret set {KEY}")


if __name__ == "__main__":
    main()
