#!/usr/bin/env python3
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
BUILD = ROOT / "build"


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
        import urllib.request
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
    gh = find_tool("gh", r"C:\Program Files\GitHub CLI\gh.exe")
    if not gh:
        sys.exit("Потрібен GitHub CLI: gh auth login")

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


def publish_packs():
    gh = find_tool("gh", r"C:\Program Files\GitHub CLI\gh.exe")
    if not gh:
        sys.exit("Потрібен GitHub CLI: gh auth login")

    folder = ROOT / "packs"
    index = folder / "packs.json"
    if not index.is_file():
        sys.exit(f"Немає {index}")

    packs = json.loads(index.read_text("utf-8"))
    files = [index]
    for pack in packs:
        target = folder / Path(pack["url"]).name
        if not target.is_file():
            sys.exit(f"У packs.json є {pack['id']}, але файлу {target.name} немає")
        files.append(target)
        print(f"{pack['id']}: {pack['version']} ({target.stat().st_size / 1024 ** 2:.1f} МБ)")

    exists = subprocess.run([gh, "release", "view", PACKS_TAG], cwd=ROOT,
                            capture_output=True).returncode == 0
    if not exists:
        subprocess.run([gh, "release", "create", PACKS_TAG, "--title", "Модпаки",
                        "--notes", "Файли модпаків для лаунчера. Не є релізом лаунчера.",
                        "--latest=false"], cwd=ROOT, check=True)

    subprocess.run([gh, "release", "upload", PACKS_TAG, *map(str, files), "--clobber"],
                   cwd=ROOT, check=True)
    print(f"Залито {len(files)} файлів у реліз {PACKS_TAG}")


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
    parser.add_argument("--packs", action="store_true",
                        help="залити модпаки з теки packs/ і вийти")
    options = parser.parse_args()

    if options.packs:
        publish_packs()
        return

    if options.release and not GIT:
        sys.exit("Для --release потрібен git у PATH")

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
