#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
BUILD = ROOT / "build"
PACKS = ROOT / "packs"  # локальна тека, у репозиторій не потрапляє


def find_git() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    for candidate in (r"C:\Program Files\Git\cmd\git.exe",
                      r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if Path(candidate).is_file():
            return candidate
    return None


GIT = find_git()


def git(*args) -> str | None:
    if not GIT:
        return None
    result = subprocess.run([GIT, *args], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def parts_of(version: str) -> list[int]:
    numbers = [int(number) for number in re.findall(r"\d+", version)][:4]
    return (numbers + [0, 0, 0, 0])[:4]


def repo_slug() -> str | None:
    remote = git("remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+/[^/.]+)", remote or "")
    return match.group(1) if match else None


def github_version() -> str | None:
    slug = repo_slug()
    if not slug:
        return None
    try:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{slug}/releases/latest",
            headers={"User-Agent": "qqq-craft-build", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response).get("tag_name")
    except Exception as error:
        print(f"GitHub недоступний ({error}), беру версію локально")
        return None


def tag_version() -> str | None:
    git("fetch", "--tags", "--quiet")
    tags = git("tag", "--list", "--sort=-v:refname")
    return tags.splitlines()[0] if tags else None


def file_version() -> str | None:
    try:
        match = re.search(r'VERSION\s*=\s*"([^"]+)"', (ROOT / "config.py").read_text("utf-8"))
        return match.group(1) if match and not match.group(1).startswith("0.0.0") else None
    except Exception:
        return None


def next_version(bump: bool) -> str:
    for source, value in (("релізи GitHub", github_version()),
                          ("локальні теги", tag_version()),
                          ("config.py", file_version())):
        if value:
            print(f"Остання версія: {value} ({source})")
            break
    else:
        value = "1.0.0.0"
        print("Версій не знайдено, починаю з 1.0.0.0")

    numbers = parts_of(value)
    if bump:
        numbers[2] += 1
        numbers[3] = 0
    return ".".join(map(str, numbers))


def write_version(version: str):
    BUILD.mkdir(exist_ok=True)
    config = ROOT / "config.py"
    config.write_text(re.sub(r'VERSION = "[^"]*"', f'VERSION = "{version}"',
                             config.read_text("utf-8"), count=1), "utf-8")
    (BUILD / "version.txt").write_text(VERSION_INFO.format(
        comma=", ".join(map(str, parts_of(version))), version=version), "utf-8")


EXCLUDES = ["tkinter", "numpy", "PIL", "pandas", "matplotlib", "scipy", "IPython",
            "pytest", "setuptools", "pip", "sqlite3", "unittest", "pydoc", "doctest"]


def build(version: str):
    import PyInstaller.__main__

    if sys.prefix == sys.base_prefix:
        print("Увага: збірка не у venv — PyInstaller може захопити зайві пакети.\n"
              "  python -m venv .venv && .venv\\Scripts\\pip install -r requirements.txt")

    arguments = [
        "--noconsole", "--clean", "--noupx", "--noconfirm",
        "--contents-directory=.",
        "--specpath", str(BUILD),
        "--workpath", str(BUILD / "pyinstaller"),
        "--distpath", str(ROOT / "dist"),
        "--name", "qqq-craft",
        "--add-data", f"{ROOT / 'web'}{os.pathsep}web",
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "keyring.backends.SecretService",
        *[argument for name in EXCLUDES for argument in ("--exclude-module", name)],
        str(ROOT / "launcher.py"),
    ]
    if platform.system() == "Windows":
        arguments += ["--icon", str(ROOT / "web" / "static" / "logo.ico"),
                      "--version-file", str(BUILD / "version.txt")]
    PyInstaller.__main__.run(arguments)


def find_tool(name: str, *candidates: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    return next((path for path in candidates if Path(path).is_file()), None)


def find_gh() -> str | None:
    return find_tool("gh", r"C:\Program Files\GitHub CLI\gh.exe")


def make_installer() -> Path | None:
    iscc = find_tool("iscc", r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
                     r"C:\Program Files\Inno Setup 6\ISCC.exe")
    if not iscc:
        print("Inno Setup не знайдено — інсталятор не зібрано")
        return None

    subprocess.run([iscc, "compile.iss"], cwd=ROOT, check=True)
    installer = ROOT / "dist" / "qqq-craft.exe"
    return installer if installer.is_file() else None


def publish(version: str, installer: Path | None):
    gh = find_gh()
    git("commit", "config.py", "-m", f"Release {version}")
    git("push")

    command = [gh, "release", "create", version, "--title", f"Release {version}",
               "--notes", f"Версія {version}", "--latest"]
    if installer:
        command.append(str(installer))
    else:
        print("Інсталятора немає — реліз буде без файлу")
    if subprocess.run(command, cwd=ROOT).returncode:
        sys.exit(f"Не вдалося створити реліз {version}")
    print(f"Реліз {version} опубліковано")


PACKS_TAG = "packs"


def publish_news():
    """Той самий скрипт, що й у CI — просто запущений вручну."""
    if not find_gh():
        sys.exit("Потрібен GitHub CLI: winget install GitHub.cli && gh auth login")

    missing = [name for name in ("DISCORD_TOKEN", "DISCORD_CHANNEL")
               if not os.environ.get(name)]
    if missing:
        sys.exit(f"Не задано {' і '.join(missing)}:\n"
                 '  setx DISCORD_TOKEN "..."\n  setx DISCORD_CHANNEL "..."')

    slug = repo_slug()
    if not slug:
        sys.exit("Не вдалося визначити репозиторій з origin")

    subprocess.run([sys.executable, str(ROOT / ".github" / "news.py")], cwd=ROOT,
                   env={**os.environ, "GITHUB_REPOSITORY": slug}, check=True)


OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
SERVER_PATTERN = re.compile(rf"\b(?:(?:[a-z0-9-]+\.)+[a-z]{{2,}}|{OCTET}(?:\.{OCTET}){{3}})(?::\d{{1,5}})?\b",
                            re.I)  # домен або IPv4, з портом чи без


def pack_entry(archive: Path) -> dict:
    """Усе про пак — із самого .mrpack: id — ім'я файлу, назва й версія — з експорту, сервер — з опису."""
    with zipfile.ZipFile(archive) as bundle:
        index = json.loads(bundle.read("modrinth.index.json"))
    entry = {"id": archive.stem, "name": index.get("name") or archive.stem,
             "version": index["versionId"], "url": archive.name,
             "sha1": hashlib.sha1(archive.read_bytes()).hexdigest()}
    server = SERVER_PATTERN.search(index.get("summary", ""))
    if server:
        entry["server"] = server.group(0)
    return entry


def publish_packs():
    """Збирає packs.json з усіх .mrpack у локальній теці packs/ і заливає в рухомий реліз packs —
    окремо від лаунчера. packs.json живе тільки в релізі: на диску його немає."""
    gh = find_gh()
    if not gh:
        sys.exit("Потрібен GitHub CLI: winget install GitHub.cli && gh auth login")

    archives = sorted(PACKS.glob("*.mrpack"))
    if not archives:
        sys.exit(f"У {PACKS} немає жодного .mrpack — поклади їх туди або стягни: compile.py --packs pull")

    packs = [pack_entry(archive) for archive in archives]
    for pack in packs:
        server = pack.get("server", "без сервера — допиши адресу в опис пака")
        print(f"{pack['id']}: {pack['name']} {pack['version']}, {server}")

    exists = subprocess.run([gh, "release", "view", PACKS_TAG], cwd=ROOT,
                            capture_output=True).returncode == 0
    if not exists:
        subprocess.run([gh, "release", "create", PACKS_TAG, "--title", "Модпаки",
                        "--notes", "Файли модпаків для лаунчера. Не є релізом лаунчера.",
                        "--latest=false"], cwd=ROOT, check=True)

    with tempfile.TemporaryDirectory() as temporary:
        index = Path(temporary) / "packs.json"
        index.write_text(json.dumps(packs, ensure_ascii=False, indent=2) + "\n", "utf-8")
        files = [index, *archives]
        subprocess.run([gh, "release", "upload", PACKS_TAG, *map(str, files), "--clobber"],
                       cwd=ROOT, check=True)
    print(f"Залито {len(files)} файлів у реліз {PACKS_TAG}")


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "qqq-craft-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def pull_packs():
    """Стягує актуальні паки з релізу packs у локальну теку packs/. Реліз публічний — gh не потрібен.
    Локальний файл, що відрізняється від релізного, не губиться: лишається поруч як .bak."""
    slug = repo_slug()
    if not slug:
        sys.exit("Не вдалося визначити репозиторій з origin")

    base = f"https://github.com/{slug}/releases/download/{PACKS_TAG}/"
    try:
        index = json.loads(fetch(base + "packs.json"))
    except Exception as error:
        sys.exit(f"Не вдалося отримати список паків з релізу {PACKS_TAG}: {error}")

    PACKS.mkdir(exist_ok=True)
    for pack in index:
        target = PACKS / Path(pack["url"]).name
        label = f"{target.name} ({pack['version']})"
        if target.is_file() and hashlib.sha1(target.read_bytes()).hexdigest() == pack["sha1"]:
            print(f"{label}: вже актуальний")
            continue

        data = fetch(base + pack["url"])
        if hashlib.sha1(data).hexdigest() != pack["sha1"]:
            sys.exit(f"{label}: завантажений файл не збігається з sha1 з packs.json")
        if target.is_file():
            backup = target.with_name(target.name + ".bak")
            target.replace(backup)
            print(f"{label}: локальний файл відрізнявся, збережено як {backup.name}")
        target.write_bytes(data)
        print(f"{label}: завантажено")


VERSION_INFO = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({comma}), prodvers=({comma}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904b0', [
      StringStruct('CompanyName', 'QQQ-CRAFT'),
      StringStruct('FileDescription', 'Launcher for QQQ-CRAFT project'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'QQQ-CRAFT'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026'),
      StringStruct('OriginalFilename', 'qqq-craft.exe'),
      StringStruct('ProductName', 'QQQ-CRAFT'),
      StringStruct('ProductVersion', '{version}')
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true",
                        help="підняти версію, зібрати і опублікувати реліз")
    parser.add_argument("--version", help="задати версію вручну, наприклад 1.0.29.0")
    parser.add_argument("--packs", nargs="?", const="push", choices=("push", "pull"),
                        help="модпаки в локальній теці packs/: без значення (push) — залити в реліз, "
                             "pull — стягнути актуальні з релізу")
    parser.add_argument("--news", action="store_true",
                        help="оновити новини з Discord і вийти")
    options = parser.parse_args()

    if options.packs:
        pull_packs() if options.packs == "pull" else publish_packs()
        return

    if options.news:
        publish_news()
        return

    if options.release:
        if not GIT:
            sys.exit("Для --release потрібен git у PATH")
        if not find_gh():
            sys.exit("Для --release потрібен GitHub CLI: winget install GitHub.cli && gh auth login")

    version = options.version or next_version(bump=options.release)
    if not GIT and not options.version:
        print("git не знайдено — версію взято з config.py "
              "(можна задати вручну: --version 1.0.29.0)")
    print(f"Версія: {version}")
    write_version(version)
    build(version)

    binary = ROOT / "dist" / "qqq-craft" / ("qqq-craft.exe" if platform.system() == "Windows"
                                            else "qqq-craft")
    if not binary.is_file():
        sys.exit("Збірка не створила виконуваний файл")
    print(f"Готово: {binary}")

    installer = make_installer() if platform.system() == "Windows" else None
    if installer:
        print(f"Інсталятор: {installer}")

    if options.release:
        publish(version, installer)


if __name__ == "__main__":
    main()