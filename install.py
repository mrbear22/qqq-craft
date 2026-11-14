"""Мережа і встановлення: HTTP з кешем, гра, Java, мод-лоадери, модпаки."""
import hashlib
import json
import logging
import lzma
import os
import re
import shutil
import tarfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NamedTuple

import minecraft_launcher_lib as mll
import requests

from config import (ARCH, CACHE_DIR, EXE_SUFFIX, INSTANCES_DIR, LATEST_RELEASE, NEWS_INDEX, OS_NAME,
                    PACKS_RELEASE, USER_AGENT, VERSION, make_executable)

log = logging.getLogger(__name__)


class InstallError(Exception):
    pass


# ── Мережа ────────────────────────────────────────────────────────────────────

TIMEOUT = (10, 60)
INDEX_TIMEOUT = (5, 15)
ATTEMPTS = 4
WORKERS = 12
MIRRORS = {"https://libraries.minecraft.net": ["https://repo1.maven.org/maven2"]}
AVATARS = ("https://minotar.net/helm/{}/36.png",
           "https://mc-heads.net/avatar/{}/36",
           "https://crafatar.com/avatars/{}?size=36&overlay")
AVATAR_TTL = 86400

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def _checksum(path: Path, algorithm: str = "sha1") -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid(path: Path, sha1: str | None, size: int | None, deep: bool = True) -> bool:
    """deep=False — лише наявність і розмір: контрольні суми рахуються, коли версія змінилась."""
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    return not (sha1 and deep) or _checksum(path) == sha1


def _alternatives(url: str) -> list[str]:
    for origin, mirrors in MIRRORS.items():
        if url.startswith(origin):
            return [mirror + url[len(origin):] for mirror in mirrors]
    return []


def download(url: str, dest: Path, sha1: str | None = None, size: int | None = None,
             decompress_lzma: bool = False, revalidate: bool = False) -> bool:
    """revalidate — спитати сервер, чи файл змінився, замість сліпої довіри кешу."""
    if _valid(dest, sha1, size) and not revalidate:
        return False

    tag = dest.with_name(dest.name + ".etag")
    headers = {}
    if revalidate and dest.is_file() and tag.is_file():
        headers["If-None-Match"] = tag.read_text("utf-8")

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    failure = None

    for attempt in range(ATTEMPTS):
        for candidate in [url, *_alternatives(url)]:
            try:
                with session.get(candidate, stream=True, timeout=TIMEOUT,
                                 headers=headers) as response:
                    if response.status_code == 304:
                        return False
                    response.raise_for_status()
                    if response.headers.get("ETag"):
                        tag.write_text(response.headers["ETag"], "utf-8")
                    with open(part, "wb") as handle:
                        for chunk in response.iter_content(1 << 16):
                            handle.write(chunk)
                if decompress_lzma:
                    raw = part.with_suffix(".raw")
                    with lzma.open(part) as source, open(raw, "wb") as target:
                        shutil.copyfileobj(source, target)
                    os.replace(raw, part)
                if sha1 and _checksum(part) != sha1:
                    raise InstallError(f"невірна контрольна сума: {candidate}")
                os.replace(part, dest)
                return True
            except Exception as error:
                failure = error
                log.warning("Збій завантаження %s: %s", candidate, error)
        time.sleep(2 ** attempt)

    part.unlink(missing_ok=True)
    raise InstallError(f"Не вдалося завантажити {dest.name}: {failure}")


def download_all(jobs: list[dict], progress, label: str, deep: bool = True):
    jobs = [job for job in jobs
            if not _valid(job["dest"], job.get("sha1"), job.get("size"), deep)]
    if not jobs:
        return
    total = len(jobs)
    progress(f"{label}: {total} файлів", 0)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(download, job["url"], job["dest"], job.get("sha1"),
                               job.get("size"), job.get("lzma", False)) for job in jobs]
        for done, future in enumerate(as_completed(futures), 1):
            future.result()
            if done % 5 == 0 or done == total:
                progress(f"{label} {done}/{total}", done / total * 100)


def fetch_json(url: str) -> dict:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_cached_json(url: str, cache: Path):
    """Умовний запит за ETag: якщо нічого не змінилось, сервер відповідає 304 і береться копія з диска."""
    tag = cache.with_name(cache.name + ".etag")
    headers = {}
    if cache.is_file() and tag.is_file():
        headers["If-None-Match"] = tag.read_text("utf-8")
    response = session.get(url, timeout=INDEX_TIMEOUT, headers=headers)
    if response.status_code == 304:
        return json.loads(cache.read_text("utf-8"))
    response.raise_for_status()
    data = response.json()
    cache.write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    if response.headers.get("ETag"):
        tag.write_text(response.headers["ETag"], "utf-8")
    else:
        tag.unlink(missing_ok=True)
    return data


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def check_update() -> dict | None:
    try:
        response = session.head(LATEST_RELEASE, timeout=8, allow_redirects=True)
        response.raise_for_status()
    except Exception:
        return None
    latest = response.url.rstrip("/").rsplit("/", 1)[-1]
    if not latest[:1].isdigit():
        return None
    as_tuple = lambda value: tuple(int(part) if part.isdigit() else 0 for part in value.split("."))
    return {"outdated": as_tuple(latest) > as_tuple(VERSION), "latest": latest, "url": response.url}


def avatar_path(uuid: str) -> Path:
    return CACHE_DIR / f"head-{uuid}.png"


def fetch_avatar(uuid: str) -> bytes | None:
    cached = avatar_path(uuid)
    if cached.is_file() and time.time() - cached.stat().st_mtime < AVATAR_TTL:
        return cached.read_bytes()
    for template in AVATARS:
        try:
            response = session.get(template.format(uuid), timeout=5)
            response.raise_for_status()
            cached.write_bytes(response.content)
            return response.content
        except Exception as error:
            log.debug("Голова недоступна з %s: %s", template, error)
    return cached.read_bytes() if cached.is_file() else None


NEWS_CACHE = CACHE_DIR / "news.json"
_news_refreshing = threading.Lock()


def _refresh_news() -> list | None:
    try:
        return fetch_cached_json(NEWS_INDEX, NEWS_CACHE)
    except Exception as error:
        log.error("Не вдалося завантажити новини: %s", error)
        return None


def _refresh_news_once():
    if _news_refreshing.acquire(blocking=False):
        try:
            _refresh_news()
        finally:
            _news_refreshing.release()


def news() -> list:
    """Збережені новини віддаються одразу, свіжі підтягуються у фоні до наступного відкриття."""
    cached = _read_json(NEWS_CACHE)
    if cached is None:
        return _refresh_news() or []
    threading.Thread(target=_refresh_news_once, daemon=True).start()
    return cached


# ── Гра, Java, лоадери ────────────────────────────────────────────────────────

VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ASSETS_BASE = "https://resources.download.minecraft.net"
JVM_MANIFEST = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
ADOPTIUM = "https://api.adoptium.net/v3/binary/latest"
MOJANG_RUNTIMES = {("windows", "x64"): "windows-x64", ("windows", "x86"): "windows-x86",
                   ("windows", "arm64"): "windows-arm64", ("linux", "x64"): "linux",
                   ("linux", "x86"): "linux-i386"}
ADOPTIUM_ARCH = {"x64": "x64", "x86": "x64", "arm64": "aarch64"}
ADOPTIUM_ARCHIVE = {"windows": "zip", "linux": "tar.gz"}
META_LOADERS = {"fabric-loader": "https://meta.fabricmc.net/v2/versions/loader",
                "quilt-loader": "https://meta.quiltmc.org/v3/versions/loader"}
MLL_LOADERS = ("forge", "neoforge")
JAVA_BINARY = f"java{EXE_SUFFIX}"


def version_path(game_dir: Path, version_id: str) -> Path:
    return game_dir / "versions" / version_id / f"{version_id}.json"


def read_version(game_dir: Path, version_id: str) -> dict:
    path = version_path(game_dir, version_id)
    if not path.is_file():
        entry = next((item for item in fetch_json(VERSION_MANIFEST)["versions"]
                      if item["id"] == version_id), None)
        if entry is None:
            raise InstallError(f"Версію {version_id} не знайдено у маніфесті Mojang")
        download(entry["url"], path, entry.get("sha1"))
    return json.loads(path.read_text("utf-8"))


def _maven_url(base: str, name: str) -> tuple[str, str]:
    group, artifact, version, *classifier = name.split(":")
    suffix = f"-{classifier[0]}" if classifier else ""
    path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{suffix}.jar"
    return base.rstrip("/") + "/" + path, path


def _rules_allow(rules: list) -> bool:
    allowed = not rules
    for rule in rules:
        target = rule.get("os", {})
        if target.get("name", OS_NAME) == OS_NAME and target.get("arch", ARCH) == ARCH:
            allowed = rule["action"] == "allow"
    return allowed


class Installer:
    """Ставить у game_dir версію гри, ресурси, Java і лоадер.
    deep=False — файли перевіряються лише на наявність і розмір, без контрольних сум."""

    def __init__(self, game_dir: Path, progress, deep: bool = True):
        self.game_dir, self.progress, self.deep = game_dir, progress, deep

    def _fetch(self, jobs: list[dict], label: str):
        download_all(jobs, self.progress, label, self.deep)

    def version(self, version_id: str) -> dict:
        data = read_version(self.game_dir, version_id)
        parent = {}
        if data.get("inheritsFrom"):
            parent = self.version(data["inheritsFrom"])
            if "jar" not in data and "client" not in data.get("downloads", {}):
                data["jar"] = data["inheritsFrom"]
                version_path(self.game_dir, version_id).write_text(json.dumps(data), "utf-8")

        jobs = []
        for library in data.get("libraries", []):
            if not _rules_allow(library.get("rules", [])):
                continue
            artifact = library.get("downloads", {}).get("artifact")
            if artifact:
                jobs.append({"url": artifact["url"],
                             "dest": self.game_dir / "libraries" / artifact["path"],
                             "sha1": artifact.get("sha1"), "size": artifact.get("size")})
            elif library.get("url"):
                url, path = _maven_url(library["url"], library["name"])
                jobs.append({"url": url, "dest": self.game_dir / "libraries" / path})

        client = data.get("downloads", {}).get("client")
        if client:
            jobs.append({"url": client["url"],
                         "dest": self.game_dir / "versions" / version_id / f"{version_id}.jar",
                         "sha1": client.get("sha1"), "size": client.get("size")})

        logging_file = data.get("logging", {}).get("client", {}).get("file")
        if logging_file:
            jobs.append({"url": logging_file["url"],
                         "dest": self.game_dir / "assets" / "log_configs" / logging_file["id"],
                         "sha1": logging_file.get("sha1")})

        self._fetch(jobs, "Файли гри")
        self._assets(data if data.get("assetIndex") else parent)
        return data

    def _assets(self, data: dict):
        index = data.get("assetIndex")
        if not index:
            return
        assets = self.game_dir / "assets"
        index_path = assets / "indexes" / f"{index['id']}.json"
        download(index["url"], index_path, index.get("sha1"))
        objects = json.loads(index_path.read_text("utf-8"))["objects"].values()
        self._fetch([{"url": f"{ASSETS_BASE}/{entry['hash'][:2]}/{entry['hash']}",
                      "dest": assets / "objects" / entry["hash"][:2] / entry["hash"],
                      "sha1": entry["hash"], "size": entry.get("size")} for entry in objects],
                    "Ресурси")

    def java(self, minecraft: str) -> str:
        java = read_version(self.game_dir, minecraft).get("javaVersion", {})
        component = java.get("component", "java-runtime-delta")
        major = java.get("majorVersion", 21)
        platform_key = MOJANG_RUNTIMES.get((OS_NAME, ARCH))

        if platform_key:
            root = self.game_dir / "runtime" / component
            executable = root / "bin" / JAVA_BINARY
            if executable.is_file():
                return str(executable)
            manifest = fetch_json(JVM_MANIFEST).get(platform_key, {}).get(component)
            if manifest:
                self.progress(f"Завантаження Java {major}", 0)
                files = fetch_json(manifest[0]["manifest"]["url"])["files"]
                jobs, executables = [], []
                for name, entry in files.items():
                    target = root / name
                    if entry["type"] == "directory":
                        target.mkdir(parents=True, exist_ok=True)
                    if entry["type"] != "file":
                        continue
                    downloads = entry["downloads"]
                    jobs.append({"url": (downloads.get("lzma") or downloads["raw"])["url"],
                                 "dest": target, "sha1": downloads["raw"]["sha1"],
                                 "lzma": "lzma" in downloads})
                    if entry.get("executable"):
                        executables.append(target)
                self._fetch(jobs, "Java")
                for path in executables:
                    make_executable(path)
                if executable.is_file():
                    return str(executable)

        return shutil.which("java") or self._adoptium(major)

    def _adoptium(self, major: int) -> str:
        root = self.game_dir / "runtime" / f"adoptium-{major}"
        existing = next(root.rglob(f"bin/{JAVA_BINARY}"), None) if root.exists() else None
        if existing:
            return str(existing)

        machine = ADOPTIUM_ARCH[ARCH]
        self.progress(f"Завантаження Java {major} (Adoptium)", 0)
        archive = CACHE_DIR / f"adoptium-{major}-{machine}.{ADOPTIUM_ARCHIVE[OS_NAME]}"
        download(f"{ADOPTIUM}/{major}/ga/{OS_NAME}/{machine}/jre/hotspot/normal/eclipse", archive)
        root.mkdir(parents=True, exist_ok=True)
        opener = zipfile.ZipFile if archive.suffix == ".zip" else tarfile.open
        with opener(archive) as bundle:
            bundle.extractall(root)

        executable = next(root.rglob(f"bin/{JAVA_BINARY}"), None)
        if not executable:
            raise InstallError("Не вдалося підготувати Java")
        make_executable(executable)
        return str(executable)

    def loader(self, dependencies: dict) -> str:
        minecraft = dependencies["minecraft"]

        for key, meta in META_LOADERS.items():
            if key not in dependencies:
                continue
            version_id = f"{key}-{dependencies[key]}-{minecraft}"
            path = version_path(self.game_dir, version_id)
            if not path.is_file():
                self.progress(f"Встановлення {key} {dependencies[key]}", 0)
                profile = fetch_json(f"{meta}/{minecraft}/{dependencies[key]}/profile/json")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(profile), "utf-8")
            self.version(version_id)
            return version_id

        for key in MLL_LOADERS:
            if key not in dependencies:
                continue
            self.version(minecraft)
            java = self.java(minecraft)
            self.progress(f"Встановлення {key} {dependencies[key]}", 0)
            loader = mll.mod_loader.get_mod_loader(key)
            version_id = loader.get_installed_version(minecraft, dependencies[key])
            if not version_path(self.game_dir, version_id).is_file():
                loader.install(minecraft, str(self.game_dir), loader_version=dependencies[key],
                               java=java, callback={"setStatus": lambda text: self.progress(text, None)})
            self.version(version_id)
            return version_id

        self.version(minecraft)
        return minecraft


# ── Модпаки ───────────────────────────────────────────────────────────────────

MANIFEST = ".qqq-pack.json"
UPDATABLE = ("mods", "config")
PROTECTED = ("options.txt", "servers.dat")
OVERRIDES = ("overrides/", "client-overrides/")


def keep_existing(relative: str, target: Path, update: bool) -> bool:
    """Наявний файл перезаписується лише з новою версією паку, options.txt і servers.dat — ніколи."""
    return target.exists() and (not update or relative.replace("\\", "/") in PROTECTED)


class LaunchTarget(NamedTuple):
    version_id: str
    game_dir: Path
    java: str


OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
SERVER_PATTERN = re.compile(rf"\b(?:(?:[a-z0-9-]+\.)+[a-z]{{2,}}|{OCTET}(?:\.{OCTET}){{3}})(?::\d{{1,5}})?\b",
                            re.I)  # домен або IPv4, з портом чи без


def describe(archive: Path) -> dict:
    """Усе про пак — із самого .mrpack: назва й версія — з експорту, сервер — з опису."""
    with zipfile.ZipFile(archive) as bundle:
        index = json.loads(bundle.read("modrinth.index.json"))
    server = SERVER_PATTERN.search(index.get("summary", ""))
    return {"name": index.get("name"), "version": index["versionId"],
            "server": server.group(0) if server else None}


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    version: str
    url: str
    size: int
    stamp: str
    server: str | None = None

    @classmethod
    def read(cls, asset: dict) -> "Pack":
        """Із релізу — лише файл і його відбиток; id — ім'я файлу, решта — з самого архіву в кеші."""
        pack_id = asset["name"].removesuffix(".mrpack")
        digest = asset.get("digest") or ""
        stamp = hashlib.sha1(f"{digest}{asset['updated_at']}{asset['size']}".encode()).hexdigest()[:12]
        pack = cls(pack_id, pack_id, "", asset["browser_download_url"], asset["size"], stamp)
        archive = pack.archive
        try:
            if download(pack.url, archive, size=pack.size):
                if digest.startswith("sha256:") and _checksum(archive, "sha256") != digest[7:]:
                    raise InstallError(f"невірна контрольна сума: {pack.url}")
                for stale in archive.parent.iterdir():
                    if stale != archive:
                        stale.unlink(missing_ok=True)
            details = describe(archive)
        except Exception:
            archive.unlink(missing_ok=True)  # зіпсований архів не лишається в кеші назавжди
            raise
        return replace(pack, name=details["name"] or pack_id, version=details["version"],
                       server=details["server"])

    @property
    def archive(self) -> Path:
        return CACHE_DIR / "packs" / self.id / f"{self.stamp}.mrpack"

    @property
    def instance(self) -> "Instance":
        return Instance(self.id)

    def to_json(self) -> dict:
        return asdict(self) | {"status": self.instance.status(self.version)}


def list_packs() -> list[Pack]:
    """Паки — це .mrpack-файли релізу packs, окремого індексу немає. Відповідь GitHub кешується:
    без мережі чи з вичерпаним лімітом запитів список береться з диска."""
    cache = CACHE_DIR / "packs-release.json"
    try:
        release = fetch_cached_json(PACKS_RELEASE, cache)
    except Exception as error:
        response = getattr(error, "response", None)
        if response is not None and response.status_code == 404:
            log.warning("Релізу з паками немає: %s", PACKS_RELEASE)
            cache.unlink(missing_ok=True)
            return []
        log.error("Не вдалося отримати список паків: %s", error)
        release = _read_json(cache, {})

    packs = []
    for asset in release.get("assets", []):
        if not asset["name"].endswith(".mrpack"):
            continue
        try:
            packs.append(Pack.read(asset))
        except Exception as error:
            log.error("Пак %s пропущено: %s", asset["name"], error)
    return sorted(packs, key=lambda pack: pack.id)


class Instance:
    """Встановлений модпак: тека гри і маніфест .qqq-pack.json."""

    def __init__(self, pack_id: str):
        self.dir = INSTANCES_DIR / pack_id

    @property
    def manifest(self) -> dict:
        return _read_json(self.dir / MANIFEST, {})

    def _save(self, manifest: dict):
        (self.dir / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")

    def status(self, latest: str) -> dict:
        manifest = self.manifest
        return {"installed": bool(manifest),
                "linked": manifest.get("linked", True),
                "version": manifest.get("version"),
                "outdated": bool(manifest) and manifest.get("version") != latest}

    def set_linked(self, linked: bool):
        manifest = self.manifest
        if not manifest:
            raise InstallError("Модпак ще не встановлено")
        self._save(manifest | {"linked": linked})

    def wipe(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def install(self, pack: Pack, progress, force: bool = False) -> LaunchTarget:
        self.dir.mkdir(parents=True, exist_ok=True)
        manifest = self.manifest
        update = manifest.get("version") != pack.version

        if manifest and not manifest.get("linked", True) and not force:
            progress("Модпак відвʼязано — файли не перевіряються", 100)
            java = Installer(self.dir, progress).java(manifest["minecraft"])
            return LaunchTarget(manifest["version_id"], self.dir, java)

        # Та сама версія — як у Modrinth: без мережі й без контрольних сум, лише докачати відсутнє.
        # Повна перевірка — при оновленні паку або примусовому запуску.
        deep = update or force
        installer = Installer(self.dir, progress, deep)
        progress(f"Завантаження {pack.name}", 0)
        archive = pack.archive
        download(pack.url, archive, size=pack.size)

        root = self.dir.resolve()
        with zipfile.ZipFile(archive) as bundle:
            index = json.loads(bundle.read("modrinth.index.json"))
            minecraft = index["dependencies"]["minecraft"]
            version_id = installer.loader(index["dependencies"])
            java = installer.java(minecraft)

            jobs, provided = [], set()
            for entry in index["files"]:
                if entry.get("env", {}).get("client", "required") == "unsupported":
                    continue
                target = (self.dir / entry["path"]).resolve()
                if not target.is_relative_to(root):
                    raise InstallError(f"Небезпечний шлях у паку: {entry['path']}")
                if keep_existing(entry["path"], target, update):
                    continue
                jobs.append({"url": entry["downloads"][0], "dest": target,
                             "sha1": entry["hashes"].get("sha1"), "size": entry.get("fileSize")})
                provided.add(entry["path"])
            download_all(jobs, progress, "Моди", deep)

            progress("Розпакування конфігів", None)
            for name in bundle.namelist():
                prefix = next((prefix for prefix in OVERRIDES if name.startswith(prefix)), None)
                if not prefix or name.endswith("/"):
                    continue
                relative = name[len(prefix):]
                target = self.dir / relative
                if keep_existing(relative, target, update):
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(name))
                provided.add(relative)

        if update:
            self._prune(provided, version_id)
        self._save({"version": pack.version, "version_id": version_id,
                    "minecraft": minecraft, "linked": True})
        return LaunchTarget(version_id, self.dir, java)

    def _prune(self, provided: set[str], version_id: str):
        """Після оновлення лишається тільки те, що є в паку, і лише поточна версія гри."""
        for folder in UPDATABLE:
            root = self.dir / folder
            for item in root.rglob("*") if root.is_dir() else []:
                relative = item.relative_to(self.dir).as_posix()
                if item.is_file() and relative not in provided:
                    item.unlink(missing_ok=True)
                    log.info("Прибрано зайве: %s", relative)

        keep, current = set(), version_id
        while current and current not in keep:
            keep.add(current)
            current = read_version(self.dir, current).get("inheritsFrom", "")
        for folder in (self.dir / "versions").iterdir():
            if folder.is_dir() and folder.name not in keep:
                shutil.rmtree(folder, ignore_errors=True)
                log.info("Прибрано стару версію: %s", folder.name)
