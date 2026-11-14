#!/usr/bin/env python3
"""Точка входу: Flask-маршрути для інтерфейсу, вікно і стан застосунку."""
import base64
import json
import logging
import os
import queue
import socket
import sys
import threading
import time
import traceback
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

import config
import game
import install
from config import (APP_DIR, BASE_DIR, FLASK_PORT, INSTANCES_DIR, LEGACY_DIRS, LOGS_DIR, Settings,
                    VERSION, alert, ask, max_ram_gb, open_path, screen_modes)

STATUS_TTL = 30
PACKS_TTL = 600  # список паків — запит до API GitHub, а без токена їх лише 60 на годину з однієї адреси
LEGACY_SIGNS = ("qqq-craft.exe", "launcher.exe", "unins000.exe", "saves", "mods", "versions")

log = logging.getLogger("launcher")
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
app = Flask(__name__, static_folder=RESOURCES / "web/static",
            template_folder=RESOURCES / "web/templates")


class State:
    """Спільний стан для потоків Flask, фонових задач і вікна."""

    def __init__(self):
        self.lock = threading.Lock()
        self.account = game.Account()
        self.packs: list[install.Pack] = []
        self.packs_time = float("-inf")
        self.servers: dict = {}
        self.servers_time = float("-inf")
        self.subscribers: set[queue.Queue] = set()
        self.window = None

    def load_packs(self, force: bool = False) -> list[install.Pack]:
        # Під замком: паралельні запити чекають на один запит до мережі, а не дублюють його.
        with self.lock:
            if force or time.monotonic() - self.packs_time > PACKS_TTL:
                self.packs = install.list_packs()
                self.packs_time = time.monotonic()
            return self.packs

    def find_pack(self, pack_id: str) -> install.Pack | None:
        return next((pack for pack in self.packs if pack.id == pack_id), None)

    def server_status(self) -> dict:
        if time.monotonic() - self.servers_time >= STATUS_TTL:
            servers = {pack.id: pack.server for pack in self.packs if pack.server}
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.servers = dict(zip(servers, pool.map(game.server_status, servers.values())))
            self.servers_time = time.monotonic()
        return self.servers

    def notify(self, message: str, percent: float | None = None):
        payload = json.dumps({"message": message, "percent": percent})
        with self.lock:
            channels = list(self.subscribers)
        for channel in channels:
            channel.put(payload)

    def subscribe(self, channel: queue.Queue, active: bool):
        with self.lock:
            (self.subscribers.add if active else self.subscribers.discard)(channel)


state = State()


def avatar_uri() -> str:
    if not state.account.uuid:
        return ""
    cached = install.avatar_path(state.account.uuid)
    if not cached.is_file():
        return "/head"
    return "data:image/png;base64," + base64.b64encode(cached.read_bytes()).decode()


def warm_avatar():
    if state.account.uuid:
        install.fetch_avatar(state.account.uuid)


def body() -> dict:
    return request.get_json(silent=True) or {}


def result(error: str | None = None):
    return jsonify({"success": False, "error": error} if error else {"success": True})


# ── Маршрути ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    settings = Settings.load()
    modes = screen_modes()
    if settings.window not in modes:
        modes.append(settings.window)
    return render_template("index.html", settings=settings, ram_max=max_ram_gb(),
                           window_choices=modes, account=state.account.name, head=avatar_uri(),
                           version=VERSION)


@app.route("/events")
def events():
    channel: queue.Queue = queue.Queue()
    state.subscribe(channel, True)

    def stream():
        try:
            while True:
                yield f"data: {channel.get()}\n\n"
        finally:
            state.subscribe(channel, False)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/packs")
def api_packs():
    packs = state.load_packs()
    chosen = Settings.load().pack
    selected = chosen if state.find_pack(chosen) else (packs[0].id if packs else "")
    return jsonify({"packs": [pack.to_json() for pack in packs], "selected": selected})


@app.route("/api/news")
def api_news():
    return jsonify(install.news())


@app.route("/status")
def status():
    return jsonify(state.server_status())


@app.route("/login", methods=["POST"])
def login():
    try:
        game.open_login_window(state.account.begin_login())
    except Exception as error:
        log.error("Login error: %s", error)
        return result(str(error))
    return result()


@app.route("/auth/callback")
def auth_callback():
    code, auth_state = request.args.get("code"), request.args.get("state")
    if not code or not auth_state:
        return render_template("callback.html", title="Помилка авторизації",
                               message="Відсутні параметри авторизації", success=False)
    ok, reason = state.account.complete_login(code, auth_state)
    if ok:
        warm_avatar()
        state.notify("auth_success")
    return render_template("callback.html",
                           title="Авторизація успішна!" if ok else "Помилка авторизації",
                           message="Можете закрити це вікно" if ok else reason, success=ok)


@app.route("/head")
def head():
    image = install.fetch_avatar(state.account.uuid) if state.account.uuid else None
    if not image:
        return "", 404
    return Response(image, mimetype="image/png", headers={"Cache-Control": "max-age=86400"})


@app.route("/logout", methods=["POST"])
def logout():
    state.account.logout()
    return result()


PACK_ACTIONS = {"unlink": lambda instance: instance.set_linked(False),
                "relink": lambda instance: instance.set_linked(True),
                "reinstall": install.Instance.wipe}


@app.route("/pack/<action>", methods=["POST"])
def pack_action(action):
    pack = state.find_pack(body().get("pack", ""))
    if not pack:
        return result("Модпак не знайдено")
    if action not in PACK_ACTIONS:
        return result("Невідома дія")
    try:
        PACK_ACTIONS[action](pack.instance)
    except Exception as error:
        return result(str(error))
    return result()


@app.route("/start", methods=["POST"])
def start():
    data = body()
    settings = Settings.parse(data)
    pack = state.find_pack(settings.pack)
    if not pack:
        return result("Модпак не обрано")
    if not state.account.logged_in:
        return result("Потрібна авторизація Microsoft")
    settings.save()

    def worker():
        try:
            target = pack.instance.install(pack, state.notify, force=bool(data.get("force")))
            state.notify("Запуск гри", 100)
            game.launch(target, settings, state.account, pack.server, state.notify)
            state.notify("Гру запущено", 100)
            threading.Timer(3.0, close).start()
        except Exception as error:
            log.exception("Помилка запуску")
            state.notify(f"Помилка: {error}")

    threading.Thread(target=worker, daemon=True).start()
    return result()


@app.route("/folder", methods=["POST"])
def folder():
    pack_id = body().get("pack", "")
    target = INSTANCES_DIR / pack_id if pack_id else INSTANCES_DIR
    target.mkdir(parents=True, exist_ok=True)
    open_path(target)
    return result()


@app.route("/external")
def external():
    url = urllib.parse.unquote(request.args.get("url", ""))
    if url.startswith(("http://", "https://")):
        open_path(url)
    return "", 204


@app.route("/window/<action>")
def window_action(action):
    if state.window and action == "minimize":
        state.window.minimize()
    elif action == "close":
        threading.Timer(0.2, close).start()
    return "", 204


@app.route("/close")
def close_route():
    threading.Timer(0.5, close).start()
    return result()


# ── Старт і завершення ────────────────────────────────────────────────────────

def legacy_dirs() -> list[Path]:
    protected = [path.resolve() for path in (APP_DIR, BASE_DIR, Path(sys.executable).parent)]
    return [path for path in LEGACY_DIRS if path.is_dir() and not any(
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
        size = sum(item.stat().st_size for item in old.rglob("*") if item.is_file()) / 1024 ** 3
        log.info("Стара тека: %s (%.1f ГБ)", old, size)
        if ask(f"Файли попередньої версії лаунчера ({size:.1f} ГБ) лишились тут:\n"
               f"{old}\n\nСвіти, скріншоти й записи можна перенести звідти вручну.\n"
               "Відкрити цю папку?"):
            open_path(old)
        break

    marker.write_text("", "utf-8")


def offer_update(shown: threading.Event):
    update = install.check_update()
    if not update or not update["outdated"]:
        return
    shown.set()
    if ask(f"Доступна нова версія лаунчера {update['latest']}.\nЗавантажити її зараз?"):
        open_path(update["url"])
        close()


def close():
    try:
        if state.window:
            state.window.destroy()
        else:
            os._exit(0)
    except Exception:
        os._exit(0)


def open_window():
    url = f"http://127.0.0.1:{FLASK_PORT}/"
    try:
        import webview
        state.window = webview.create_window(f"QQQ — Час стати легендою! ({VERSION})", url,
                                             width=1280, height=720, min_size=(1000, 640),
                                             frameless=True, easy_drag=False,
                                             background_color="#4b2e2a")
        webview.start()
    except Exception as error:
        log.warning("Вікно недоступне (%s), відкриваю браузер", error)
        import webbrowser
        webbrowser.open(url)
        threading.Event().wait()


def excepthook(kind, value, tb):
    if issubclass(kind, KeyboardInterrupt):
        return sys.__excepthook__(kind, value, tb)
    alert(f"Помилка: {value}",
          f"Версія {VERSION}\n" + "".join(traceback.format_exception(kind, value, tb)))


def background(target, *args) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return thread


def main():
    config.init()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(LOGS_DIR / "launcher.log", encoding="utf-8")])
    sys.excepthook = excepthook

    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", FLASK_PORT)) == 0:
            alert("Лаунчер вже запущений")
            sys.exit(0)

    notify_legacy()

    update_shown = threading.Event()
    checker = background(offer_update, update_shown)
    background(warm_avatar)
    background(state.load_packs, True)
    background(lambda: app.run(host="127.0.0.1", port=FLASK_PORT, threaded=True,
                               debug=False, use_reloader=False))

    checker.join(timeout=3)
    if update_shown.is_set():
        checker.join()

    open_window()


if __name__ == "__main__":
    main()
