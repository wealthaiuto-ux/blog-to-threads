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
    bullets = infographic.get("bullets", [])
    bullets_text = "\n".join(
        f"  Row {i+1}: icon {b.get('icon', '•')}  text \"{b.get('text', '')}\""
        for i, b in enumerate(bullets)
    )
    bullet_count = len(bullets)
    title = infographic.get("title", "")
    subtitle = infographic.get("subtitle", "")
    cta = infographic.get("cta", "")
    return f"""Create a VERTICAL (4:5 aspect ratio) Japanese infographic poster.

EXACTLY follow the style of the first reference image (navy header, multiple icon rows on white background, orange CTA box at bottom, friendly Japanese male character cutout at bottom-right, small "詳しくはブログで → chanko06.com" footer).

The male character (second reference image) MUST appear in the bottom-right area, smiling and gesturing. Keep his face, hair, outfit identical to the reference.

CONTENT (render Japanese characters CLEARLY and READABLY):

[Header — navy blue band, white large title and orange small subtitle]
Title: 「{title}」
Subtitle: 「{subtitle}」

[Body — white background, {bullet_count} rows. Each row: a flat icon on the left and short Japanese text on the right]
{bullets_text}

[CTA box — orange rounded rectangle near bottom, dark text]
「{cta}」

[Footer — small gray text, centered below CTA]
「詳しくはブログで → chanko06.com」

REQUIREMENTS:
- 4:5 portrait, ~1024x1280
- Clean flat illustration, pastel/cream background between header and CTA
- High-contrast, MOBILE-LEGIBLE Japanese text
- No realistic shading, no photo style
- All Japanese text must be rendered exactly as specified above
"""


def generate(prompt_fallback: str, output_path: Path, *,
             infographic: dict | None = None) -> bool:
    api_key = os.environ.get("GOOGLE_AI_API_KEY")
    if not api_key:
        print("GOOGLE_AI_API_KEY 未設定 → 画像生成スキップ", file=sys.stderr)
        return False

    parts: list[dict] = []
    # スタイル参考は最初に
    if STYLE_REF.exists():
        parts.append(_inline(STYLE_REF))
    # 主人公参考
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
