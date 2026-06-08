"""Nano Banana (gemini-3.1-flash-image-preview) でインフォグラフィックを生成する。

戦略:
  1. assets/style_reference.jpg をスタイル参考として送る（毎回同じレイアウト感を維持）
  2. assets/character.jpg を主人公キャラ参考として送る
  3. infographic 構造化データを文字起こしして "exactly this style" と指示
  4. 失敗（テキストのみ返答など）したら呼び元がリトライする

infographic = {
  "title": "10〜12字",
  "subtitle": "15字以内",
  "bullets": [{"icon": "🛢", "text": "..."}, ...],  # 5要素
  "cta": "25字以内"
}
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

MODEL = "gemini-3.1-flash-image-preview"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "generated"
ASSETS = ROOT / "assets"

# 主人公リファレンス（bottom-right に配置されるキャラ）
_BUNDLED_CHAR = ASSETS / "character.jpg"
_LOCAL_CHAR = Path.home() / ".claude" / "assets" / "主人公.JPG"
CHARACTER_REF = _BUNDLED_CHAR if _BUNDLED_CHAR.exists() else _LOCAL_CHAR

# スタイルリファレンス（インフォグラフィック全体のレイアウト見本）
STYLE_REF = ASSETS / "style_reference.jpg"


def _inline(path: Path, mime: str = "image/jpeg") -> dict:
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
        }
    }


def _build_infographic_prompt(infographic: dict) -> str:
    """ユーザー指定テンプレートに沿った日本語インフォグラフィックのプロンプトを組み立てる。"""
    bullets = infographic.get("bullets", [])
    bullets_text = "\n".join(
        f'  Step {i+1}: icon hint "{b.get("icon", "•")}", bullet text "{b.get("text", "")}"'
        for i, b in enumerate(bullets)
    )
    bullet_count = len(bullets)
    title = infographic.get("title", "")
    subtitle = infographic.get("subtitle", "")
    cta = infographic.get("cta", "")

    return f"""A vector illustration of a friendly informational infographic poster, in Japanese.

Character: On the LEFT side, the SAME young East Asian man shown in the second reference image — short black hair (slightly faded on the sides), wearing a casual light gray suit with a white band-collar shirt underneath. He is smiling warmly and gesturing with his right hand open towards the right side of the image, as if explaining or presenting the information. Keep his face, hair, outfit identical to the reference.

Layout: The right side and center of the image contain structured information panels.

Top: A bold, eye-catching main title in dark blue and yellow.
  Main title (Japanese): 「{title}」
  Sub-headline (Japanese, smaller, yellow accent): 「{subtitle}」

Middle: {bullet_count} rounded rectangular text boxes organized vertically. Each box contains a simple flat-design icon on the left, a large step number (1, 2, 3 ...) and a short Japanese bullet text. Use these contents in order:
{bullets_text}

Bottom: A horizontal banner with a calendar icon and the following Japanese call-to-action text: 「{cta}」. Below it, a small footer line: 「詳しくはブログで → chanko06.com」.

Background & Style: A light, clean background. The overall style is flat, corporate but friendly, modern, and easy to read. High quality, sharp, no messy text. All Japanese characters must be rendered CLEARLY and READABLY — no scrambled or invented glyphs. Aspect ratio: 4:5 portrait, around 1024x1280.
"""


def generate(prompt_fallback: str, output_path: Path, *,
             infographic: dict | None = None) -> bool:
    api_key = os.environ.get("GOOGLE_AI_API_KEY")
    if not api_key:
        print("GOOGLE_AI_API_KEY 未設定 → 画像生成スキップ", file=sys.stderr)
        return False

    parts: list[dict] = []
    # キャラクターリファレンス（主人公）を最初に
    # ※ 過去のスタイルリファレンスは新プロンプトと競合するため送らない
    if CHARACTER_REF.exists():
        parts.append(_inline(CHARACTER_REF))

    if infographic and infographic.get("bullets"):
        text = _build_infographic_prompt(infographic)
    else:
        text = (
            f"Create a vertical (4:5) Japanese infographic poster about: {prompt_fallback}. "
            "Use the first reference image's exact layout (navy header, 5 icon rows, orange CTA, character bottom-right). "
            "Flat illustration, mobile-legible Japanese text, no photo style."
        )
    parts.append({"text": text})

    payload = {"contents": [{"parts": parts}]}
    req = Request(
        f"{API_URL}?key={api_key}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
    try:
        with urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except HTTPError as e:
        print(f"Gemini API error: {e.code}", file=sys.stderr)
        return False

    for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
        inline = part.get("inline_data") or part.get("inlineData")
        if inline:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(base64.b64decode(inline["data"]))
            return True
    print("Gemini が画像を返さなかった（テキストのみ）", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True, help="フォールバックプロンプト")
    ap.add_argument("--infographic", help="infographic JSONファイル")
    args = ap.parse_args()

    info = None
    if args.infographic:
        info = json.loads(Path(args.infographic).read_text(encoding="utf-8"))

    out = OUT_DIR / f"thread_{int(time.time())}.png"
    ok = generate(args.prompt, out, infographic=info)
    print(str(out) if ok else "")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
