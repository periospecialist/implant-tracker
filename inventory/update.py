"""Release discovery and safe hand-off to the Windows PowerShell updater."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

from ._build import BUILD_COMMIT, BUILD_TAG, GITHUB_REPOSITORY


ASSET_NAME = "ImplantInventory-Windows.zip"


def latest_release(timeout=10):
    request = Request(
        f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ImplantInventory-Updater"},
    )
    with urlopen(request, timeout=timeout) as response:
        release = json.load(response)
    asset = next((item for item in release.get("assets", []) if item.get("name") == ASSET_NAME), None)
    if not asset:
        raise RuntimeError(f"The latest release does not contain {ASSET_NAME}.")
    return {
        "tag": str(release["tag_name"]),
        "name": str(release.get("name") or release["tag_name"]),
        "url": str(asset["browser_download_url"]),
        "page": str(release.get("html_url") or ""),
        "available": str(release["tag_name"]) != BUILD_TAG,
    }


def _safe_extract(archive, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if destination != target and destination not in target.parents:
                raise RuntimeError("The downloaded update contains an unsafe path.")
        bundle.extractall(destination)


def download_release(release, update_root):
    update_root = Path(update_root)
    update_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="download-", dir=update_root))
    archive = work / ASSET_NAME
    request = Request(release["url"], headers={"User-Agent": "ImplantInventory-Updater"})
    with urlopen(request, timeout=60) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    extracted = work / "package"
    extracted.mkdir()
    _safe_extract(archive, extracted)
    if not (extracted / "ImplantInventory.exe").is_file() or not (extracted / "Update.ps1").is_file():
        raise RuntimeError("The downloaded update is incomplete.")
    return extracted


def launch_installer(source, config_path=None):
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise RuntimeError("Updates can only be installed from the packaged Windows application.")
    executable = Path(sys.executable).resolve()
    source = Path(source).resolve()
    updater = source / "Update.ps1"
    command = [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(updater),
        "-Source", str(source), "-Destination", str(executable.parent),
        "-ProcessId", str(os.getpid()), "-Launch", str(executable.name),
    ]
    if config_path:
        command.extend(["-ConfigPath", str(Path(config_path).resolve())])
    subprocess.Popen(command, cwd=str(source), close_fds=True)


def build_summary():
    return f"{BUILD_TAG} ({BUILD_COMMIT[:8]})"
