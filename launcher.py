#!/usr/bin/env python3
import base64
import json
import logging
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request

import game
import install
from config import (APP_DIR, BASE_DIR, CACHE_DIR, FLASK_PORT, INSTANCES_DIR, IS_WINDOWS, LOGS_DIR,
                    NEWS_INDEX, Settings, USER_AGENT, VERSION, check_update, max_ram_gb,
                    open_path, screen_modes)

STATUS_TTL = 30
PACKS_TTL = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOGS_DIR / "launcher.log", encoding="utf-8")],
)
log = logging.getLogger("launcher")

RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
app = Flask(__name__, static_folder=RESOURCES / "web/static",
            template_folder=RESOURCES / "web/templates")
account = game.Account()
subscribers: set[queue.Queue] = set()
status_cache = {"time": 0.0, "data": {}}
packs_fetched = 0.0
dialog_shown = threading.Event()
window = None
packs_cache: list[dict] = []


def _dialog(text: str, question: bool) -> bool:
    if IS_WINDOWS:
        import ctypes
        style = 0x24 if question else 0x10
        return ctypes.windll.user32.MessageBoxW(None, text, "QQQ-CRAFT", style) == 6

    for tool, args in (("zenity", ["--question" if question else "--error", f"--text={text}"]),
                       ("kdialog", ["--yesno" if question else "--error", text])):
        if shutil.which(tool):
            return subprocess.run([tool, *args]).returncode == 0

    print(text, file=sys.stderr)
    if not question:
        return False
    return input("Продовжити? [y/N] ").strip().lower().startswith("y")


def alert(title: str, details: str = ""):
    text = f"{title}\n\n{details}" if details else title
    log.error("%s %s", title, details)
    try:
        _dialog(text, question=False)
    except Exception as error:
        log.warning("Діалог недоступний: %s", error)
        print(text, file=sys.stderr)


def ask(question: str) -> bool:
    try:
        return _dialog(question, question=True)
    except Exception as error:
        log.warning("Діалог недоступний: %s", error)
        return False


def excepthook(kind, value, tb):
    if issubclass(kind, KeyboardInterrupt):
        return sys.__excepthook__(kind, value, tb)
    alert(f"Помилка: {value}",
          f"Версія {VERSION}\n" + "".join(traceback.format_exception(kind, value, tb)))


sys.excepthook = excepthook


def notify(message: str, percent: float | None = None):
    payload = json.dumps({"message": message, "percent": percent})
    for channel in list(subscribers):
        channel.put(payload)


def find_pack(pack_id: str) -> dict | None:
    return next((pack for pack in packs_cache if pack["id"] == pack_id), None)


@app.route("/events")
def events():
    channel: queue.Queue = queue.Queue()
    subscribers.add(channel)

    def stream():
        try:
            while True:
                yield f"data: {channel.get()}\n\n"
        finally:
            subscribers.discard(channel)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/")
def index():
    settings = Settings.load()
    modes = screen_modes()
    if settings.window not in modes:
        modes.append(settings.window)

    return render_template("index.html", settings=settings, ram_max=max_ram_gb(),
                           window_choices=modes, account=account.name, head=head_uri(),
                           version=VERSION)


def load_packs(force: bool = False) -> list[dict]:
    global packs_cache, packs_fetched
    if force or time.monotonic() - packs_fetched > PACKS_TTL:
        packs_cache = install.list_packs()
        packs_fetched = time.monotonic()
    for pack in packs_cache:
        pack["status"] = install.pack_status(pack)
    return packs_cache


@app.route("/api/packs")
def api_packs():
    load_packs()
    settings = Settings.load()
    selected = settings.pack if find_pack(settings.pack) else (
        packs_cache[0]["id"] if packs_cache else "")
    return jsonify({"packs": packs_cache, "selected": selected})


@app.route("/api/news")
def api_news():
    return jsonify(load_news())


@app.route("/status")
def status():
    now = time.monotonic()
    if now - status_cache["time"] < STATUS_TTL:
        return jsonify(status_cache["data"])

    servers = {pack["id"]: pack["server"] for pack in packs_cache if pack.get("server")}
    with ThreadPoolExecutor(max_workers=8) as pool:
        pinged = dict(zip(servers, pool.map(game.server_status, servers.values())))

    status_cache.update(time=now, data=pinged)
    return jsonify(pinged)


@app.route("/login", methods=["POST"])
def login():
    try:
        game.open_login_window(account.begin_login())
        return jsonify({"success": True})
    except Exception as error:
        log.error("Login error: %s", error)
        return jsonify({"success": False, "error": str(error)})


@app.route("/auth/callback")
def auth_callback():
    code, state = request.args.get("code"), request.args.get("state")
    if not code or not state:
        return render_template("callback.html", title="Помилка авторизації",
                               message="Відсутні параметри авторизації", success=False)
    ok, reason = account.complete_login(code, state)
    if ok:
        warm_head()
        notify("auth_success")
    return render_template("callback.html",
                           title="Авторизація успішна!" if ok else "Помилка авторизації",
                           message="Можете закрити це вікно" if ok else reason, success=ok)


def fetch_head(uuid: str) -> bytes | None:
    cached = CACHE_DIR / f"head-{uuid}.png"
    if cached.is_file() and time.time() - cached.stat().st_mtime < 86400:
        return cached.read_bytes()

    for url in (f"https://minotar.net/helm/{uuid}/36.png",
                f"https://mc-heads.net/avatar/{uuid}/36",
                f"https://crafatar.com/avatars/{uuid}?size=36&overlay"):
        try:
            response = requests.get(url, timeout=5, headers={"User-Agent": USER_AGENT})
            response.raise_for_status()
            cached.write_bytes(response.content)
            return response.content
        except Exception as error:
            log.debug("Голова недоступна з %s: %s", url, error)

    return cached.read_bytes() if cached.is_file() else None


def head_uri() -> str:
    if not account.uuid:
        return ""
    cached = CACHE_DIR / f"head-{account.uuid}.png"
    if not cached.is_file():
        return "/head"
    return "data:image/png;base64," + base64.b64encode(cached.read_bytes()).decode()


def warm_head():
    if account.uuid:
        fetch_head(account.uuid)


@app.route("/head")
def head():
    if not account.uuid:
        return ("", 404)
    image = fetch_head(account.uuid)
    if not image:
        return ("", 404)
    return Response(image, mimetype="image/png", headers={"Cache-Control": "max-age=86400"})


@app.route("/logout", methods=["POST"])
def logout():
    account.logout()
    return jsonify({"success": True})


@app.route("/pack/<action>", methods=["POST"])
def pack_action(action):
    pack = find_pack((request.get_json(silent=True) or {}).get("pack", ""))
    if not pack:
        return jsonify({"success": False, "error": "Модпак не знайдено"})
    try:
        if action == "unlink":
            install.set_linked(pack["id"], False)
        elif action == "relink":
            install.set_linked(pack["id"], True)
        elif action == "reinstall":
            install.wipe(pack["id"])
        else:
            return jsonify({"success": False, "error": "Невідома дія"})
    except Exception as error:
        return jsonify({"success": False, "error": str(error)})
    return jsonify({"success": True})


@app.route("/window/<action>")
def window_action(action):
    if window and action == "minimize":
        window.minimize()
    elif action == "close":
        threading.Timer(0.2, close).start()
    return ("", 204)


@app.route("/start", methods=["POST"])
def start():
    body = request.get_json(silent=True) or {}
    settings = Settings.parse(body)
    pack = find_pack(settings.pack)
    if not pack:
        return jsonify({"success": False, "error": "Модпак не обрано"})
    if not account.logged_in:
        return jsonify({"success": False, "error": "Потрібна авторизація Microsoft"})
    settings.save()

    def worker():
        try:
            version_id, game_dir, java = install.install_pack(pack, notify,
                                                              force=bool(body.get("force")))
            notify("Запуск гри", 100)
            game.launch(version_id, game_dir, java, settings, account, pack.get("server"), notify)
            notify("Гру запущено", 100)
            threading.Timer(3.0, close).start()
        except Exception as error:
            log.exception("Помилка запуску")
            notify(f"Помилка: {error}")

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"success": True})


@app.route("/folder", methods=["POST"])
def folder():
    pack_id = (request.get_json(silent=True) or {}).get("pack", "")
    target = INSTANCES_DIR / pack_id if pack_id else INSTANCES_DIR
    target.mkdir(parents=True, exist_ok=True)
    open_path(target)
    return jsonify({"success": True})


@app.route("/external")
def external():
    url = urllib.parse.unquote(request.args.get("url", ""))
    if url.startswith(("http://", "https://")):
        open_path(url)
    return ("", 204)


@app.route("/close")
def close_route():
    threading.Timer(0.5, close).start()
    return jsonify({"success": True})


def load_news() -> list:
    cache = CACHE_DIR / "news.json"
    try:
        response = requests.get(NEWS_INDEX, timeout=10, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
        news = response.json()
        cache.write_text(json.dumps(news, ensure_ascii=False), "utf-8")
        return news
    except Exception as error:
        log.error("Не вдалося завантажити новини: %s", error)
    try:
        return json.loads(cache.read_text("utf-8"))
    except Exception:
        return []


LEGACY_SIGNS = ("qqq-craft.exe", "launcher.exe", "unins000.exe", "saves", "mods", "versions")


def legacy_dirs() -> list[Path]:
    if IS_WINDOWS:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        found = [local / "Programs" / "qqq-craft", local / "qqq-craft"]
    else:
        found = [Path.home() / ".local/share/qqq-craft", Path.home() / ".qqq-craft"]

    protected = [path.resolve() for path in (APP_DIR, BASE_DIR, Path(sys.executable).parent)]
    return [path for path in found if path.is_dir() and not any(
        current == path.resolve() or path.resolve() in current.parents or
        current in path.resolve().parents for current in protected)]


def notify_legacy():
    """Один раз повідомляє про стару теку. Нічого не переносить і не видаляє."""
    marker = BASE_DIR / ".legacy-checked"
    if marker.is_file():
        return

    for old in legacy_dirs():
        if not any((old / name).exists() for name in LEGACY_SIGNS):
            continue
        size = sum(item.stat().st_size for item in old.rglob("*") if item.is_file())
        log.info("Стара тека: %s (%.1f ГБ)", old, size / 1024 ** 3)
        if ask(f"Файли попередньої версії лаунчера ({size / 1024 ** 3:.1f} ГБ) лишились тут:\n"
               f"{old}\n\nСвіти, скріншоти й записи можна перенести звідти вручну.\n"
               "Відкрити цю папку?"):
            open_path(old)
        break

    marker.write_text("", "utf-8")


def offer_update():
    update = check_update()
    if not update or not update["outdated"]:
        return
    dialog_shown.set()
    if ask(f"Доступна нова версія лаунчера {update['latest']}.\nЗавантажити її зараз?"):
        open_path(update["url"])
        close()


def close():
    try:
        if window:
            window.destroy()
        else:
            os._exit(0)
    except Exception:
        os._exit(0)


def open_window():
    global window
    url = f"http://127.0.0.1:{FLASK_PORT}/"
    try:
        import webview
        window = webview.create_window(f"QQQ — Час стати легендою! ({VERSION})", url,
                                       width=1280, height=720, min_size=(1000, 640),
                                       frameless=True, easy_drag=False,
                                       background_color="#4b2e2a")
        webview.start()
    except Exception as error:
        log.warning("Вікно недоступне (%s), відкриваю браузер", error)
        import webbrowser
        webbrowser.open(url)
        threading.Event().wait()


def main():
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", FLASK_PORT)) == 0:
            alert("Лаунчер вже запущений")
            sys.exit(0)

    notify_legacy()

    checker = threading.Thread(target=offer_update, daemon=True)
    checker.start()
    threading.Thread(target=warm_head, daemon=True).start()
    threading.Thread(target=lambda: load_packs(force=True), daemon=True).start()

    threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=FLASK_PORT, threaded=True,
                               debug=False, use_reloader=False),
        daemon=True).start()

    checker.join(timeout=3)
    if dialog_shown.is_set():
        checker.join()

    open_window()


if __name__ == "__main__":
    main()
