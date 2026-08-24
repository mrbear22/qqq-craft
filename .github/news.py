#!/usr/bin/env python3
"""Дзеркалить новинний канал Discord у реліз packs. Токен живе тільки в секретах CI."""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import markdown
import requests
from PIL import Image

API = "https://discord.com/api/v10"
MENTIONS = re.compile(r"@everyone|@here|<@[!&]?\d+>|<#\d+>")
BULLET = re.compile(r"^\s*(?:[-*+]|\d+\.)\s")
IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MAX_WIDTH = 1000
QUALITY = 80
TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL = os.environ["DISCORD_CHANNEL"]
REPO = os.environ["GITHUB_REPOSITORY"]
TAG = "news"
LIMIT = 15

OUT = Path("news")
OUT.mkdir(exist_ok=True)
session = requests.Session()
session.headers["User-Agent"] = "qqq-craft-news/1.0"


def fetch_messages() -> list[dict]:
    response = session.get(f"{API}/channels/{CHANNEL}/messages", params={"limit": LIMIT},
                           headers={"Authorization": f"Bot {TOKEN}"}, timeout=20)
    response.raise_for_status()
    return response.json()


def pick_image(embed: dict, attachments: list[dict]) -> str:
    """Відео та інші вкладення пропускаємо — у новинах показується лише картинка."""
    for candidate in (embed.get("image", {}).get("url"), embed.get("thumbnail", {}).get("url")):
        if candidate:
            return candidate
    for attachment in attachments:
        content_type = attachment.get("content_type", "")
        name = attachment.get("filename", "").lower()
        if content_type.startswith("image/") or name.endswith(IMAGES):
            return attachment.get("url", "")
    return ""


def clean(text: str) -> str:
    return MENTIONS.sub("", text or "").strip()


def mirror_image(url: str) -> str:
    """Посилання Discord на вкладення живуть добу — тому картинка переїжджає до нас."""
    if not url:
        return ""
    try:
        image = session.get(url, timeout=30)
        image.raise_for_status()
    except Exception as error:
        print(f"Картинка недоступна: {error}", file=sys.stderr)
        return ""

    digest = hashlib.sha1(image.content).hexdigest()[:12]
    suffix = Path(url.split("?")[0]).suffix.lower() or ".png"
    name = compress(image.content, digest) or f"news-{digest}{suffix}"
    if not (OUT / name).exists():
        (OUT / name).write_bytes(image.content)
    return f"https://github.com/{REPO}/releases/download/{TAG}/{name}"


def compress(data: bytes, digest: str) -> str | None:
    """Скріншоти з гри приходять оригіналами — у стрічці вистачає 1000px webp."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if getattr(image, "n_frames", 1) > 1:
                return None
            if image.width > MAX_WIDTH:
                height = round(image.height * MAX_WIDTH / image.width)
                image = image.resize((MAX_WIDTH, height), Image.LANCZOS)
            name = f"news-{digest}.webp"
            image.convert("RGB").save(OUT / name, "WEBP", quality=QUALITY, method=6)
            print(f"  {name}: {len(data) // 1024} КБ → {(OUT / name).stat().st_size // 1024} КБ")
            return name
    except Exception as error:
        print(f"Не вдалося стиснути: {error}", file=sys.stderr)
        return None


def open_lists(text: str) -> str:
    """Markdown вимагає порожній рядок перед списком — але лише перед першим пунктом."""
    lines, out = (text or "").split("\n"), []
    for index, line in enumerate(lines):
        previous = lines[index - 1] if index else ""
        if BULLET.match(line) and previous.strip() and not BULLET.match(previous):
            out.append("")
        out.append(line)
    return "\n".join(out)


def to_html(text: str) -> str:
    html = markdown.markdown(open_lists(text), extensions=["nl2br", "sane_lists"])
    return re.sub(r'<a\s+href=["\']([^"\']+)["\']',
                  lambda match: f'<a data-external="{match.group(1)}" href="#"', html, flags=re.I)


def build(message: dict) -> dict | None:
    embed = (message.get("embeds") or [{}])[0]
    lines = [line for line in clean(message.get("content", "")).split("\n") if line.strip()]
    title = embed.get("title") or (lines[0][:120] if lines else "")
    body = clean(embed.get("description", "")) or "\n".join(lines[1:])
    image = pick_image(embed, message.get("attachments") or [])

    if not title and not image:
        return None
    return {
        "title": title,
        "description": to_html(body),
        "image": mirror_image(image),
        "timestamp": (message.get("timestamp") or "").split(".")[0].replace("T", " "),
        "reactions": [{"emoji": (item.get("emoji") or {}).get("name", ""),
                       "count": item.get("count", 0)} for item in message.get("reactions", [])],
    }


def publish(files: list[Path]):
    if subprocess.run(["gh", "release", "view", TAG], capture_output=True).returncode:
        subprocess.run(["gh", "release", "create", TAG, "--title", "Новини",
                        "--notes", "Дзеркало новинного каналу Discord для лаунчера.",
                        "--latest=false"], check=True)
    subprocess.run(["gh", "release", "upload", TAG, *map(str, files), "--clobber"], check=True)


def main():
    items = [item for item in map(build, fetch_messages()) if item]
    (OUT / "news.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")
    print(f"Новин: {len(items)}, картинок: {len(list(OUT.glob('news-*')))}")
    publish(sorted(OUT.iterdir()))


if __name__ == "__main__":
    main()
