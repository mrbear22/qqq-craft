#!/usr/bin/env python3
"""Дзеркалить новинний канал Discord у реліз packs. Токен живе тільки в секретах CI."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import markdown
import requests

API = "https://discord.com/api/v10"
TOKEN = os.environ["DISCORD_TOKEN"]
CHANNEL = os.environ["DISCORD_CHANNEL"]
REPO = os.environ["GITHUB_REPOSITORY"]
TAG = "packs"
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

    suffix = Path(url.split("?")[0]).suffix or ".png"
    name = f"news-{hashlib.sha1(image.content).hexdigest()[:12]}{suffix}"
    (OUT / name).write_bytes(image.content)
    return f"https://github.com/{REPO}/releases/download/{TAG}/{name}"


def to_html(text: str) -> str:
    html = markdown.markdown(text or "")
    return re.sub(r'<a\s+href=["\']([^"\']+)["\']',
                  lambda match: f'<a data-external="{match.group(1)}" href="#"', html, flags=re.I)


def build(message: dict) -> dict | None:
    embed = (message.get("embeds") or [{}])[0]
    attachment = (message.get("attachments") or [{}])[0]
    title = embed.get("title") or (message.get("content") or "").split("\n")[0][:120]
    body = embed.get("description") or "\n".join((message.get("content") or "").split("\n")[1:])
    image = embed.get("image", {}).get("url") or attachment.get("url", "")

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
        subprocess.run(["gh", "release", "create", TAG, "--title", "Модпаки",
                        "--notes", "Файли модпаків і новини для лаунчера.",
                        "--latest=false"], check=True)
    subprocess.run(["gh", "release", "upload", TAG, *map(str, files), "--clobber"], check=True)


def main():
    items = [item for item in map(build, fetch_messages()) if item]
    (OUT / "news.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), "utf-8")
    print(f"Новин: {len(items)}, картинок: {len(list(OUT.glob('news-*')))}")
    publish(sorted(OUT.iterdir()))


if __name__ == "__main__":
    main()
