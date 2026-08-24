import hashlib
import json
import logging
import lzma
import os
import platform
import shutil
import stat
import tarfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

import minecraft_launcher_lib as mll
import requests

from config import CACHE_DIR, INSTANCES_DIR, IS_WINDOWS, PACKS_INDEX, USER_AGENT

log = logging.getLogger(__name__)

VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
ASSETS_BASE = "https://resources.download.minecraft.net"
JVM_MANIFEST = ("https://launchermeta.mojang.com/v1/products/java-runtime/"
                "2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
FABRIC_META = "https://meta.fabricmc.net/v2/versions/loader"
QUILT_META = "https://meta.quiltmc.org/v3/versions/loader"
ADOPTIUM = "https://api.adoptium.net/v3/binary/latest"

MIRRORS = {"https://libraries.minecraft.net": ["https://repo1.maven.org/maven2"]}

TIMEOUT = (10, 60)
ATTEMPTS = 4
WORKERS = 12

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


class InstallError(Exception):
    pass


MANIFEST = ".qqq-pack.json"
UPDATABLE = ("mods/", "config/")


def is_updatable(relative: str) -> bool:
    """mods/ і config/ пак перезаписує; решту — лише якщо файлу ще немає."""
    return relative.replace("\\", "/").startswith(UPDATABLE)


def _alternatives(url: str) -> list[str]:
    for origin, mirrors in MIRRORS.items():
        if url.startswith(origin):
            return [mirror + url[len(origin):] for mirror in mirrors]
    return []


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid(path: Path, sha1: str | None, size: int | None) -> bool:
    if not path.is_file():
        return False
    if size is not None and path.stat().st_size != size:
        return False
    if sha1 is not None:
        return _sha1(path) == sha1
    return True


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
    urls = [url] + _alternatives(url)
    failure = None

    for attempt in range(ATTEMPTS):
        for candidate in urls:
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
                    with lzma.open(part) as source, open(part.with_suffix(".raw"), "wb") as target:
                        shutil.copyfileobj(source, target)
                    os.replace(part.with_suffix(".raw"), part)
                if sha1 and _sha1(part) != sha1:
                    raise InstallError(f"невірна контрольна сума: {candidate}")
                os.replace(part, dest)
                return True
            except Exception as error:
                failure = error
                log.warning("Збій завантаження %s: %s", candidate, error)
        time.sleep(2 ** attempt)

    part.unlink(missing_ok=True)
    raise InstallError(f"Не вдалося завантажити {dest.name}: {failure}")


def download_all(jobs: list[dict], progress, label: str):
    jobs = [job for job in jobs if not _valid(job["dest"], job.get("sha1"), job.get("size"))]
    if not jobs:
        return
    done = 0
    total = len(jobs)
    progress(f"{label}: {total} файлів", 0)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(download, job["url"], job["dest"], job.get("sha1"),
                               job.get("size"), job.get("lzma", False)): job for job in jobs}
        for future in as_completed(futures):
            future.result()
            done += 1
            if done % 5 == 0 or done == total:
                progress(f"{label} {done}/{total}", done / total * 100)


def fetch_json(url: str) -> dict:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def list_packs() -> list[dict]:
    cache = CACHE_DIR / "packs.json"
    try:
        packs = fetch_json(PACKS_INDEX)
        cache.write_text(json.dumps(packs, ensure_ascii=False), "utf-8")
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else 0
        if status in (401, 403, 404, 410):
            log.warning("Індексу паків немає (%s): %s", status, PACKS_INDEX)
            cache.unlink(missing_ok=True)
            return []
        log.error("Не вдалося отримати список паків: %s", error)
        packs = _cached_packs(cache)
    except Exception as error:
        log.error("Не вдалося отримати список паків: %s", error)
        packs = _cached_packs(cache)

    for pack in packs:
        pack["url"] = urljoin(PACKS_INDEX, pack["url"])
    return packs


def _cached_packs(cache: Path) -> list[dict]:
    try:
        return json.loads(cache.read_text("utf-8"))
    except Exception:
        return []


def _maven_url(base: str, name: str) -> tuple[str, str]:
    group, artifact, version, *classifier = name.split(":")
    suffix = f"-{classifier[0]}" if classifier else ""
    path = f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}{suffix}.jar"
    return base.rstrip("/") + "/" + path, path


def _rules_allow(rules: list) -> bool:
    if not rules:
        return True
    allowed = False
    current = "windows" if IS_WINDOWS else "linux"
    for rule in rules:
        target = rule.get("os", {})
        if "name" in target and target["name"] != current:
            continue
        if "arch" in target and target["arch"] not in platform.machine().lower():
            continue
        allowed = rule["action"] == "allow"
    return allowed


def _version_json(version_id: str, game_dir: Path) -> dict:
    path = game_dir / "versions" / version_id / f"{version_id}.json"
    if not path.is_file():
        entry = next((item for item in fetch_json(VERSION_MANIFEST)["versions"]
                      if item["id"] == version_id), None)
        if entry is None:
            raise InstallError(f"Версію {version_id} не знайдено у маніфесті Mojang")
        download(entry["url"], path, entry.get("sha1"))
    return json.loads(path.read_text("utf-8"))


def install_version(version_id: str, game_dir: Path, progress) -> dict:
    data = _version_json(version_id, game_dir)
    parent = {}
    if data.get("inheritsFrom"):
        parent = install_version(data["inheritsFrom"], game_dir, progress)
        if "jar" not in data and "client" not in data.get("downloads", {}):
            data["jar"] = data["inheritsFrom"]
            path = game_dir / "versions" / version_id / f"{version_id}.json"
            path.write_text(json.dumps(data), "utf-8")

    jobs = []
    for library in data.get("libraries", []):
        if not _rules_allow(library.get("rules", [])):
            continue
        artifact = library.get("downloads", {}).get("artifact")
        if artifact:
            jobs.append({"url": artifact["url"],
                         "dest": game_dir / "libraries" / artifact["path"],
                         "sha1": artifact.get("sha1"), "size": artifact.get("size")})
        elif library.get("url"):
            url, path = _maven_url(library["url"], library["name"])
            jobs.append({"url": url, "dest": game_dir / "libraries" / path})

    client = data.get("downloads", {}).get("client")
    if client:
        jobs.append({"url": client["url"],
                     "dest": game_dir / "versions" / version_id / f"{version_id}.jar",
                     "sha1": client.get("sha1"), "size": client.get("size")})

    logging_file = data.get("logging", {}).get("client", {}).get("file")
    if logging_file:
        jobs.append({"url": logging_file["url"],
                     "dest": game_dir / "assets" / "log_configs" / logging_file["id"],
                     "sha1": logging_file.get("sha1")})

    download_all(jobs, progress, "Файли гри")
    _install_assets(data if data.get("assetIndex") else parent, game_dir, progress)
    return data


def _install_assets(data: dict, game_dir: Path, progress):
    index = data.get("assetIndex")
    if not index:
        return
    index_path = game_dir / "assets" / "indexes" / f"{index['id']}.json"
    download(index["url"], index_path, index.get("sha1"))
    objects = json.loads(index_path.read_text("utf-8"))["objects"]

    jobs = []
    for entry in objects.values():
        digest = entry["hash"]
        jobs.append({"url": f"{ASSETS_BASE}/{digest[:2]}/{digest}",
                     "dest": game_dir / "assets" / "objects" / digest[:2] / digest,
                     "sha1": digest, "size": entry.get("size")})
    download_all(jobs, progress, "Ресурси")


def _jvm_platform() -> str | None:
    machine = platform.machine().lower()
    if IS_WINDOWS:
        return {"arm64": "windows-arm64", "x86": "windows-x86"}.get(machine, "windows-x64")
    if machine in ("x86_64", "amd64"):
        return "linux"
    if machine in ("i386", "i686"):
        return "linux-i386"
    return None


def ensure_java(data: dict, game_dir: Path, progress) -> str:
    java = data.get("javaVersion", {})
    component = java.get("component", "java-runtime-delta")
    major = java.get("majorVersion", 21)
    binary = "java.exe" if IS_WINDOWS else "java"
    platform_key = _jvm_platform()

    if platform_key:
        root = game_dir / "runtime" / component
        executable = root / "bin" / binary
        if executable.is_file():
            return str(executable)
        manifest = fetch_json(JVM_MANIFEST).get(platform_key, {}).get(component)
        if manifest:
            progress(f"Завантаження Java {major}", 0)
            files = fetch_json(manifest[0]["manifest"]["url"])["files"]
            jobs, executables = [], []
            for name, entry in files.items():
                target = root / name
                if entry["type"] == "directory":
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if entry["type"] != "file":
                    continue
                source = entry["downloads"].get("lzma") or entry["downloads"]["raw"]
                jobs.append({"url": source["url"], "dest": target,
                             "sha1": entry["downloads"]["raw"]["sha1"],
                             "lzma": "lzma" in entry["downloads"]})
                if entry.get("executable"):
                    executables.append(target)
            download_all(jobs, progress, "Java")
            for path in executables:
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if executable.is_file():
                return str(executable)

    system = shutil.which("java")
    if system:
        return system
    return _install_adoptium(major, game_dir, progress, binary)


def _install_adoptium(major: int, game_dir: Path, progress, binary: str) -> str:
    root = game_dir / "runtime" / f"adoptium-{major}"
    existing = next(root.rglob(f"bin/{binary}"), None) if root.exists() else None
    if existing:
        return str(existing)

    arch = {"aarch64": "aarch64", "arm64": "aarch64", "x86_64": "x64", "amd64": "x64"}
    machine = arch.get(platform.machine().lower(), "x64")
    system = "windows" if IS_WINDOWS else "linux"
    url = f"{ADOPTIUM}/{major}/ga/{system}/{machine}/jre/hotspot/normal/eclipse"

    progress(f"Завантаження Java {major} (Adoptium)", 0)
    archive = CACHE_DIR / f"adoptium-{major}-{machine}.{'zip' if IS_WINDOWS else 'tar.gz'}"
    download(url, archive)
    root.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(root)
    else:
        with tarfile.open(archive) as bundle:
            bundle.extractall(root)

    executable = next(root.rglob(f"bin/{binary}"), None)
    if not executable:
        raise InstallError("Не вдалося підготувати Java")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(executable)


def _install_loader(dependencies: dict, game_dir: Path, progress) -> str:
    minecraft = dependencies["minecraft"]

    for key, meta, prefix in (("fabric-loader", FABRIC_META, "fabric-loader"),
                              ("quilt-loader", QUILT_META, "quilt-loader")):
        if key not in dependencies:
            continue
        loader = dependencies[key]
        version_id = f"{prefix}-{loader}-{minecraft}"
        path = game_dir / "versions" / version_id / f"{version_id}.json"
        if not path.is_file():
            progress(f"Встановлення {prefix} {loader}", 0)
            profile = fetch_json(f"{meta}/{minecraft}/{loader}/profile/json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(profile), "utf-8")
        install_version(version_id, game_dir, progress)
        return version_id

    for key in ("forge", "neoforge"):
        if key not in dependencies:
            continue
        data = install_version(minecraft, game_dir, progress)
        java = ensure_java(data, game_dir, progress)
        progress(f"Встановлення {key} {dependencies[key]}", 0)
        loader = mll.mod_loader.get_mod_loader(key)
        version_id = loader.get_installed_version(minecraft, dependencies[key])
        if not (game_dir / "versions" / version_id / f"{version_id}.json").is_file():
            loader.install(minecraft, str(game_dir), loader_version=dependencies[key], java=java,
                           callback={"setStatus": lambda text: progress(text, None)})
        install_version(version_id, game_dir, progress)
        return version_id

    install_version(minecraft, game_dir, progress)
    return minecraft


def prune_versions(game_dir: Path, version_id: str):
    """Після переїзду на нову версію гри старі теки versions/ вже нікому не потрібні."""
    keep, current = set(), version_id
    while current and current not in keep:
        keep.add(current)
        current = _version_json(current, game_dir).get("inheritsFrom", "")

    root = game_dir / "versions"
    for folder in root.iterdir() if root.is_dir() else []:
        if folder.is_dir() and folder.name not in keep:
            shutil.rmtree(folder, ignore_errors=True)
            log.info("Прибрано стару версію: %s", folder.name)


def prune(game_dir: Path, provided: set[str]):
    """У mods/ і config/ лишається тільки те, що є в паку."""
    for folder in UPDATABLE:
        root = game_dir / folder.rstrip("/")
        for item in root.rglob("*") if root.is_dir() else []:
            relative = item.relative_to(game_dir).as_posix()
            if item.is_file() and relative not in provided:
                item.unlink(missing_ok=True)
                log.info("Прибрано зайве: %s", relative)

def read_manifest(pack_id: str) -> dict:
    path = INSTANCES_DIR / pack_id / MANIFEST
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {}


def set_linked(pack_id: str, linked: bool):
    manifest = read_manifest(pack_id)
    if not manifest:
        raise InstallError("Модпак ще не встановлено")
    manifest["linked"] = linked
    (INSTANCES_DIR / pack_id / MANIFEST).write_text(
        json.dumps(manifest, ensure_ascii=False), "utf-8")


def wipe(pack_id: str):
    shutil.rmtree(INSTANCES_DIR / pack_id, ignore_errors=True)


def pack_status(pack: dict) -> dict:
    manifest = read_manifest(pack["id"])
    return {
        "installed": bool(manifest),
        "linked": manifest.get("linked", True),
        "version": manifest.get("version"),
        "outdated": bool(manifest) and manifest.get("version") != pack["version"],
    }


def install_pack(pack: dict, progress, force: bool = False) -> tuple[str, Path, str]:
    game_dir = INSTANCES_DIR / pack["id"]
    game_dir.mkdir(parents=True, exist_ok=True)
    manifest = read_manifest(pack["id"])

    if manifest and not manifest.get("linked", True) and not force:
        progress("Модпак відвʼязано — файли не перевіряються", 100)
        java = ensure_java(_version_json(manifest["minecraft"], game_dir), game_dir, progress)
        return manifest["version_id"], game_dir, java

    progress(f"Завантаження {pack['name']}", 0)
    archive = CACHE_DIR / f"{pack['id']}-{pack['version']}.mrpack"
    download(pack["url"], archive, pack.get("sha1"), revalidate=not pack.get("sha1"))

    with zipfile.ZipFile(archive) as bundle:
        index = json.loads(bundle.read("modrinth.index.json"))
        version_id = _install_loader(index["dependencies"], game_dir, progress)
        java = ensure_java(_version_json(index["dependencies"]["minecraft"], game_dir),
                           game_dir, progress)

        jobs, provided = [], set()
        for entry in index["files"]:
            if entry.get("env", {}).get("client", "required") == "unsupported":
                continue
            target = (game_dir / entry["path"]).resolve()
            if not str(target).startswith(str(game_dir.resolve())):
                raise InstallError(f"Небезпечний шлях у паку: {entry['path']}")
            if not is_updatable(entry["path"]) and target.exists():
                continue
            jobs.append({"url": entry["downloads"][0], "dest": target,
                         "sha1": entry["hashes"].get("sha1"), "size": entry.get("fileSize")})
            provided.add(entry["path"])
        download_all(jobs, progress, "Моди")

        progress("Розпакування конфігів", None)
        for name in bundle.namelist():
            for prefix in ("overrides/", "client-overrides/"):
                if not name.startswith(prefix) or name.endswith("/"):
                    continue
                relative = name[len(prefix):]
                target = game_dir / relative
                if not is_updatable(relative) and target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(name))
                provided.add(relative)

    prune(game_dir, provided)
    prune_versions(game_dir, version_id)
    (game_dir / MANIFEST).write_text(json.dumps({
        "version": pack["version"], "version_id": version_id,
        "minecraft": index["dependencies"]["minecraft"], "linked": True}, ensure_ascii=False), "utf-8")
    return version_id, game_dir, java
