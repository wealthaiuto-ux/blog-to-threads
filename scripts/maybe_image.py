"""30%確率でNano Banana画像を生成。生成パスを返す、しなかったら空文字。

GitHub Actions 上ではローカルの ~/.claude/scripts/generate_image.py が無いので、
直接 Gemini API を叩く実装にしている。リファレンス画像（主人公）も同梱できる。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

IMAGE_PROBABILITY = 0.3
MODEL = "gemini-3.1-flash-image-preview"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "generated"
# 主人公リファレンス：スキル同梱版を優先、無ければ ~/.claude/assets/主人公.JPG
_BUNDLED_REF = ROOT / "assets" / "character.jpg"
_LOCAL_REF = Path.home() / ".claude" / "assets" / "主人公.JPG"
CHARACTER_REF = _BUNDLED_REF if _BUNDLED_REF.exists() else _LOCAL_REF


def _load_reference() -> dict | None:
    if not CHARACTER_REF.exists():
        return None
    b = CHARACTER_REF.read_bytes()
    return {
        "inline_data": {
            "mime_type": "image/jpeg",
            "data": base64.b64encode(b).decode("ascii"),
        }
    }


def generate(prompt: str, output_path: Path) -> bool:
    api_key = os.environ.get("GOOGLE_AI_API_KEY")
    if not api_key:
        print("GOOGLE_AI_API_KEY 未設定 → 画像生成スキップ", file=sys.stderr)
        return False

    parts: list[dict] = [{"text": (
        f"{prompt}. flat illustration style, clean lines, soft pastel colors, "
        "light cream background, Japanese editorial style, no realistic shading."
    )}]
    ref = _load_reference()
    if ref:
        parts.insert(0, ref)
        parts[1]["text"] = (
            "Use the reference image as the main character (same outfit, hair, face). "
            + parts[1]["text"]
        )

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
    print("Gemini が画像を返さなかった", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--force", action="store_true", help="確率を無視して必ず生成")
    args = ap.parse_args()

    if not args.force and random.random() >= IMAGE_PROBABILITY:
        print("")
        return 0

    out = OUT_DIR / f"thread_{int(time.time())}.png"
    ok = generate(args.prompt, out)
    print(str(out) if ok else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
