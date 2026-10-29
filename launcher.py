from __future__ import annotations
import base64, ctypes, hashlib, html, http.server, json, os, re, shutil, subprocess, sys, threading, webbrowser, zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, urlparse, urljoin
import requests
import tkinter as tk
from tkinter import messagebox, ttk
import minecraft_launcher_lib as mclib

APP_NAME = "LEDERG STALKER"
APP_VERSION = "1.1.0"
MC_VERSION = "1.12.2"
FORGE_BUILD = "14.23.5.2861"
SERVER_HOST = "LEDERG05.aternos.me"
SERVER_PORT = 26820
REDIRECT_URI = "http://localhost:8765/callback"
JAVA_URL = "https://api.adoptium.net/v3/binary/latest/8/ga/windows/x64/jre/hotspot/normal/eclipse"
MAX_PACK_BYTES = 500 * 1024 * 1024
MAX_PACK_EXPANDED = 1500 * 1024 * 1024
MAX_PACK_ENTRIES = 12000
MAX_JAVA_BYTES = 250 * 1024 * 1024
MAX_JAVA_EXPANDED = 800 * 1024 * 1024
ALLOWED_TOP = {"mods", "config", "resourcepacks", "defaultconfigs", "modpack-manifest.json", "LEDERG-MODPACK-README.txt"}
FORBIDDEN_NAMES = {"tlauncher.exe", "tlauncher32bit.exe", "tlauncherprofiles.json", "launcher_profiles.json", "servers.dat", "servers.dat_old"}
FORBIDDEN_ROOTS = {"assets", "libraries", "versions", "runtime", "saves", "logs", "backup", "cache"}

def app_dir() -> Path:
    result = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home()) / APP_NAME
    result.mkdir(parents=True, exist_ok=True)
    return result

def resource_file(name: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name

def load_settings() -> dict:
    try:
        defaults = json.loads(resource_file("launcher_config.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        defaults = {}
    try:
        local = json.loads((app_dir() / "settings.json").read_text(encoding="utf-8-sig"))
        if isinstance(local, dict):
            defaults.update(local)
    except (OSError, json.JSONDecodeError):
        pass
    defaults.setdefault("modpack_url", "")
    defaults.setdefault("microsoft_client_id", "")
    defaults.setdefault("display_alias", "")
    return defaults

def save_settings(settings: dict) -> None:
    path = app_dir() / "settings.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

def drive_file_id(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if (parsed.hostname or "").lower() not in {"drive.google.com", "docs.google.com"}:
        return None
    match = re.search(r"/file/d/([A-Za-z0-9_-]+)", parsed.path)
    if match:
        return match.group(1)
    ident = parse_qs(parsed.query).get("id", [None])[0]
    return ident if ident and re.fullmatch(r"[A-Za-z0-9_-]+", ident) else None

def drive_download_url(url: str) -> str:
    ident = drive_file_id(url)
    if not ident:
        raise ValueError("Нужна публичная ссылка Google Drive на ZIP-файл, а не на папку.")
    resource_key = parse_qs(urlparse(url.strip()).query).get("resourcekey", [None])[0]
    result = f"https://drive.google.com/uc?export=download&id={ident}"
    return result + (f"&resourcekey={resource_key}" if resource_key else "")

def _request_drive(session: requests.Session, share_url: str):
    ident = drive_file_id(share_url)
    response = session.get(drive_download_url(share_url), stream=True, timeout=(20, 120), allow_redirects=True)
    if "text/html" in response.headers.get("Content-Type", "").lower():
        body = response.text[:4_000_000]
        token = next((v for k, v in session.cookies.items() if k.startswith("download_warning")), None)
        if not token:
            match = re.search(r'name="confirm"\s+value="([^"]+)"', body) or re.search(r'[?&]confirm=([0-9A-Za-z_-]+)', body)
            token = match.group(1) if match else None
        if token:
            response.close()
            response = session.get(f"https://drive.google.com/uc?export=download&confirm={token}&id={ident}", stream=True, timeout=(20, 120), allow_redirects=True)
        else:
            match = re.search(r'href="([^"]*(?:confirm|download)[^"]*)"', body)
            if not match:
                response.close()
                raise ValueError("Google Drive не отдаёт архив. Проверь доступ «Все, у кого есть ссылка — Читатель».")
            follow = html.unescape(match.group(1)).replace("&amp;", "&")
            response.close()
            response = session.get(urljoin("https://drive.google.com/", follow), stream=True, timeout=(20, 120), allow_redirects=True)
    response.raise_for_status()
    if "text/html" in response.headers.get("Content-Type", "").lower():
        response.close()
        raise ValueError("Google Drive вернул HTML, а не ZIP. Проверь общий доступ.")
    return response

def validate_modpack(archive_path: Path) -> list[str]:
    if not archive_path.is_file() or archive_path.stat().st_size < 4 or archive_path.stat().st_size > MAX_PACK_BYTES:
        raise ValueError("ZIP отсутствует, повреждён или превышает 500 МБ.")
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        if not entries or len(entries) > MAX_PACK_ENTRIES:
            raise ValueError("ZIP пуст или содержит слишком много файлов.")
        names, total = set(), 0
        for entry in entries:
            name = entry.filename
            rel = PurePosixPath(name)
            if rel.is_absolute() or ".." in rel.parts or "\\" in name or "\x00" in name:
                raise ValueError(f"Небезопасный путь в ZIP: {name}")
            if not rel.parts:
                continue
            first = rel.parts[0].lower()
            if first in FORBIDDEN_ROOTS:
                raise ValueError(f"ZIP содержит файлы Minecraft, а должен содержать только моды: {first}/")
            if first not in {item.lower() for item in ALLOWED_TOP}:
                raise ValueError(f"Неожиданная папка в модпаке: {rel.parts[0]}")
            low = rel.name.lower()
            if low in FORBIDDEN_NAMES or low.startswith("tlauncher") or "tl_skincape" in low:
                raise ValueError(f"Файлы TLauncher запрещены в LEDERG-модпаке: {rel.name}")
            mode = (entry.external_attr >> 16) & 0xFFFF
            if mode & 0o170000 == 0o120000:
                raise ValueError(f"Символические ссылки запрещены: {name}")
            total += entry.file_size
            if total > MAX_PACK_EXPANDED:
                raise ValueError("Распакованный модпак превышает 1,5 ГБ.")
            names.add(name.lstrip("./"))
        required = ("mods/CustomMainMenu_MC1.12.2_2.0.8.jar", "mods/SovietEra-0.6-1.12.2.jar", "config/CustomMainMenu/mainmenu.json")
        missing = [name for name in required if name not in names]
        if missing:
            raise ValueError("В ZIP отсутствуют обязательные файлы: " + ", ".join(missing))
        bad = archive.testzip()
        if bad:
            raise ValueError(f"Повреждённый файл в ZIP: {bad}")
        return sorted(name for name in names if not name.endswith("/"))

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()

def download_modpack(share_url: str, destination: Path, progress=None) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    session, digest = requests.Session(), hashlib.sha256()
    try:
        with _request_drive(session, share_url) as response:
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_PACK_BYTES:
                raise ValueError("ZIP превышает 500 МБ.")
            received = 0
            with partial.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > MAX_PACK_BYTES:
                        raise ValueError("ZIP превысил лимит 500 МБ.")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress:
                        progress(received, total)
        with partial.open("rb") as stream:
            if stream.read(2) != b"PK":
                raise ValueError("Скачанный файл не является ZIP.")
        validate_modpack(partial)
        partial.replace(destination)
        return digest.hexdigest().upper()
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        session.close()

def enforce_server_menu(path: Path) -> None:
    if not path.is_file():
        raise ValueError("В модпаке не найден файл меню LEDERG.")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    buttons = data.setdefault("buttons", {})
    for name in ("singleplayer", "multiplayer", "realms"):
        buttons.pop(name, None)
    buttons["lederg_join"] = {
        "text": "LEDERG  |  ИГРАТЬ",
        "posX": -100, "posY": 46, "width": 200, "height": 24, "alignment": "center",
        "action": {"type": "connectToServer", "ip": f"{SERVER_HOST}:{SERVER_PORT}"},
    }
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)

def extract_modpack(archive_path: Path, game_dir: Path, managed_manifest: Path) -> list[str]:
    new_files = validate_modpack(archive_path)
    root = game_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads(managed_manifest.read_text(encoding="utf-8"))
        old_files = previous.get("files", []) if isinstance(previous, dict) else []
    except (OSError, json.JSONDecodeError):
        old_files = []
    for name in old_files:
        rel = PurePosixPath(str(name))
        if not rel.parts or rel.is_absolute() or ".." in rel.parts or rel.parts[0].lower() not in ALLOWED_TOP:
            continue
        target = (root / Path(*rel.parts)).resolve()
        if target != root and root not in target.parents:
            continue
        if target.is_file():
            target.unlink()
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            rel = PurePosixPath(entry.filename)
            if not rel.parts:
                continue
            target = (root / Path(*rel.parts)).resolve()
            if target != root and root not in target.parents:
                raise ValueError(f"Путь выходит за папку игры: {entry.filename}")
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    enforce_server_menu(root / "config" / "CustomMainMenu" / "mainmenu.json")
    managed_manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = managed_manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps({"files": new_files, "sha256": sha256_file(archive_path)}, indent=2), encoding="utf-8")
    temporary.replace(managed_manifest)
    return new_files

def _download_java_archive(path: Path, progress=None) -> None:
    partial = path.with_suffix(".part")
    session = requests.Session()
    try:
        with session.get(JAVA_URL, stream=True, timeout=(20, 120), allow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("Content-Length") or 0)
            if total > MAX_JAVA_BYTES:
                raise ValueError("Java Runtime больше 250 МБ.")
            received = 0
            with partial.open("wb") as output:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > MAX_JAVA_BYTES:
                        raise ValueError("Java Runtime превысил 250 МБ.")
                    output.write(chunk)
                    if progress:
                        progress(received, total)
        with partial.open("rb") as stream:
            if stream.read(2) != b"PK":
                raise ValueError("Поставщик Java вернул не ZIP.")
        partial.replace(path)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        session.close()

def ensure_java8(progress=None) -> Path:
    runtime_root = app_dir() / "runtime" / "temurin8"
    if runtime_root.exists():
        found = next(runtime_root.rglob("javaw.exe"), None) or next(runtime_root.rglob("java.exe"), None)
        if found:
            return found
    runtime_root.parent.mkdir(parents=True, exist_ok=True)
    archive_path = app_dir() / "temurin-jre8.zip"
    if not archive_path.is_file():
        _download_java_archive(archive_path, progress)
    stage = runtime_root.with_name("temurin8.staging")
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if sum(entry.file_size for entry in entries) > MAX_JAVA_EXPANDED:
                raise ValueError("Java Runtime имеет недопустимый размер распаковки.")
            if not any(PurePosixPath(entry.filename).name.lower() == "javaw.exe" for entry in entries):
                raise ValueError("В архиве Java не найден javaw.exe.")
            for entry in entries:
                rel = PurePosixPath(entry.filename)
                if rel.is_absolute() or ".." in rel.parts or "\\" in entry.filename:
                    raise ValueError("Небезопасный путь в Java Runtime.")
                target = (stage / Path(*rel.parts)).resolve()
                if target != stage.resolve() and stage.resolve() not in target.parents:
                    raise ValueError("Java Runtime выходит за каталог установки.")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(entry) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
        shutil.rmtree(runtime_root, ignore_errors=True)
        stage.replace(runtime_root)
        found = next(runtime_root.rglob("javaw.exe"), None) or next(runtime_root.rglob("java.exe"), None)
        if not found:
            raise ValueError("Java Runtime распакован, но исполняемый файл не найден.")
        return found
    finally:
        shutil.rmtree(stage, ignore_errors=True)

def launcher_callbacks(status=None, progress=None):
    return {
        "setStatus": (lambda value: status(str(value))) if status else (lambda value: None),
        "setProgress": (lambda value: progress(float(value))) if progress else (lambda value: None),
        "setMax": (lambda value: progress(float(value), True)) if progress else (lambda value: None),
    }

def find_forge_id(game_dir: Path) -> str:
    versions_dir = game_dir / "versions"
    if versions_dir.exists():
        for folder in versions_dir.iterdir():
            profile = folder / f"{folder.name}.json"
            if not profile.is_file() or "forge" not in folder.name.lower() or MC_VERSION not in folder.name:
                continue
            try:
                data = json.loads(profile.read_text(encoding="utf-8-sig"))
                libs = " ".join(str(item.get("name", "")) for item in data.get("libraries", []) if isinstance(item, dict))
                if (MC_VERSION in libs and "forge" in libs.lower()) or data.get("inheritsFrom") == MC_VERSION:
                    return folder.name
            except (OSError, json.JSONDecodeError):
                pass
    for item in mclib.utils.get_installed_versions(str(game_dir)):
        ident = str(item.get("id", ""))
        if MC_VERSION in ident and "forge" in ident.lower():
            return ident
    raise RuntimeError("Forge 1.12.2 не найден после установки.")

def ensure_game_installed(progress_status=None, progress_value=None):
    game_dir = app_dir() / "game"
    game_dir.mkdir(parents=True, exist_ok=True)
    java_exe = ensure_java8(lambda done, total: progress_status(f"Скачивание Java 8: {done // (1024 * 1024)} МБ") if progress_status else None)
    callbacks = launcher_callbacks(progress_status, progress_value)
    if progress_status:
        progress_status("Установка официального Minecraft 1.12.2…")
    mclib.install.install_minecraft_version(MC_VERSION, str(game_dir), callback=callbacks)
    try:
        version_id = find_forge_id(game_dir)
    except RuntimeError:
        version_id = ""
    if not version_id:
        try:
            choices = [str(value) for value in mclib.forge.list_forge_versions()]
        except Exception:
            choices = []
        forge = next((value for value in choices if FORGE_BUILD in value and MC_VERSION in value), f"{MC_VERSION}-{FORGE_BUILD}")
        if progress_status:
            progress_status(f"Установка Forge {FORGE_BUILD}…")
        mclib.forge.install_forge_version(forge, str(game_dir), callback=callbacks, java=str(java_exe))
        version_id = find_forge_id(game_dir)
    return game_dir, version_id

class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

def dpapi_protect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Шифрование токенов Windows доступно только на Windows.")
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "LEDERG Launcher", None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)

def dpapi_unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("Расшифровка токенов Windows доступна только на Windows.")
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)

def account_path() -> Path:
    return app_dir() / "account.json"

def save_account(profile: dict) -> None:
    token = str(profile.get("refresh_token") or "")
    if not token or not profile.get("name") or not profile.get("id"):
        raise ValueError("Microsoft не вернул полный профиль Minecraft.")
    protected = base64.b64encode(dpapi_protect(token.encode("utf-8"))).decode("ascii")
    data = {"name": str(profile["name"]), "id": str(profile["id"]), "refresh_token_dpapi": protected}
    path = account_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)

def load_account_record():
    try:
        value = json.loads(account_path().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) and value.get("refresh_token_dpapi") else None
    except (OSError, json.JSONDecodeError):
        return None

def refresh_account(client_id: str) -> dict:
    record = load_account_record()
    if not record:
        raise ValueError("Сначала войди в Microsoft-аккаунт с Minecraft: Java Edition.")
    try:
        encrypted = base64.b64decode(record["refresh_token_dpapi"], validate=True)
        token = dpapi_unprotect(encrypted).decode("utf-8")
        profile = mclib.microsoft_account.complete_refresh(client_id, None, REDIRECT_URI, token)
        save_account(profile)
        return profile
    except Exception as exc:
        raise RuntimeError("Не удалось обновить Microsoft-сессию. Нажми «Войти Microsoft» и авторизуйся снова.\n" + str(exc)) from exc

def login_microsoft(client_id: str) -> dict:
    if not re.fullmatch(r"[0-9A-Fa-f-]{36}", client_id or ""):
        raise ValueError("Введи Client ID своего Microsoft Entra/Azure-приложения.")
    login_url, state, verifier = mclib.microsoft_account.get_secure_login_data(client_id, REDIRECT_URI)
    result, arrived = {}, threading.Event()
    parsed = urlparse(REDIRECT_URI)

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] != parsed.path:
                self.send_error(404)
                return
            result["url"] = f"{parsed.scheme}://{parsed.netloc}{self.path}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<!doctype html><meta charset='utf-8'><title>LEDERG</title>"
                "<body style='background:#090909;color:#eee;font:18px Segoe UI;padding:40px'>"
                "<h2 style='color:#e3262e'>LEDERG STALKER</h2>Вход завершён. Вернись в лаунчер и закрой эту вкладку.</body>".encode("utf-8")
            )
            arrived.set()
        def log_message(self, *_args):
            return

    server = http.server.HTTPServer((parsed.hostname or "localhost", parsed.port or 8765), CallbackHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        webbrowser.open(login_url)
        if not arrived.wait(timeout=300):
            raise TimeoutError("Время ожидания входа Microsoft истекло.")
    finally:
        server.shutdown()
        server.server_close()
    try:
        code = mclib.microsoft_account.parse_auth_code_url(result.get("url", ""), state)
        profile = mclib.microsoft_account.complete_login(client_id, None, REDIRECT_URI, code, verifier)
    except Exception as exc:
        if type(exc).__name__ == "AzureAppNotPermitted":
            raise RuntimeError("Microsoft не разрешил этому приложению использовать Minecraft API. Одного Client ID недостаточно; приложению нужно отдельное разрешение.") from exc
        if type(exc).__name__ == "AccountNotOwnMinecraft":
            raise RuntimeError("У этого Microsoft-аккаунта не обнаружена лицензия Minecraft: Java Edition.") from exc
        raise RuntimeError("Вход Microsoft не завершился: " + str(exc)) from exc
    save_account(profile)
    return profile

def build_launch_options(profile: dict, java_path: Path, game_dir: Path) -> dict:
    if not profile.get("name") or not profile.get("id") or not profile.get("access_token"):
        raise ValueError("Нет подтверждённого профиля Microsoft. Выполни вход.")
    return {
        "username": str(profile["name"]),
        "uuid": str(profile["id"]),
        "token": str(profile["access_token"]),
        "executablePath": str(java_path),
        "defaultExecutablePath": str(java_path),
        "launcherName": "LEDERG Launcher",
        "launcherVersion": APP_VERSION,
        "gameDirectory": str(game_dir),
        "server": SERVER_HOST,
        "port": str(SERVER_PORT),
        "jvmArguments": ["-Xms1G", "-Xmx4G"],
    }

class LauncherUI:
    def __init__(self):
        self.root = tk.Tk()
        alias = str(self.settings.get("display_alias", "")).strip()
        self.root.title("LEDERG STALKER | " + (alias or "Independent Launcher"))
        self.root.geometry("850x680")
        self.root.minsize(780, 630)
        self.root.configure(bg="#080808")
        self.settings = load_settings()
        self.busy = False
        self.profile_name = tk.StringVar(value=(load_account_record() or {}).get("name", "Не выполнен вход"))
        self.build()

    def build(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Red.Horizontal.TProgressbar", troughcolor="#242424", background="#e3262e", bordercolor="#242424", lightcolor="#e3262e", darkcolor="#e3262e")
        header = tk.Frame(self.root, bg="#0d0d0d", height=122)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="LEDERG", bg="#0d0d0d", fg="#e3262e", font=("Segoe UI", 30, "bold")).pack(anchor="w", padx=30, pady=(17, 0))
        tk.Label(header, text="STALKER ZONE  /  INDEPENDENT FORGE LAUNCHER", bg="#0d0d0d", fg="#bdbdbd", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=34)
        body = tk.Frame(self.root, bg="#080808")
        body.pack(fill="both", expand=True, padx=30, pady=18)
        tk.Label(body, text="КЛИЕНТСКИЙ МОДПАК", bg="#080808", fg="#f5f5f5", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(body, text="Публичная ссылка Google Drive на LEDERG-STALKER-pack.zip (доступ «Читатель»).", bg="#080808", fg="#999999", font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 7))
        row = tk.Frame(body, bg="#080808")
        row.pack(fill="x")
        self.pack_url = tk.StringVar(value=self.settings.get("modpack_url", ""))
        tk.Entry(row, textvariable=self.pack_url, bg="#171717", fg="#f2f2f2", insertbackground="#e3262e", relief="flat", font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=9, padx=(0, 8))
        tk.Button(row, text="СОХРАНИТЬ", command=self.save_settings, bg="#303030", fg="white", activebackground="#454545", relief="flat", padx=14, pady=7).pack(side="right")
        tk.Label(body, text="MICROSOFT CLIENT ID", bg="#080808", fg="#f5f5f5", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(14, 4))
        self.client_id = tk.StringVar(value=self.settings.get("microsoft_client_id", ""))
        tk.Entry(body, textvariable=self.client_id, bg="#171717", fg="#f2f2f2", insertbackground="#e3262e", relief="flat", font=("Consolas", 10)).pack(fill="x", ipady=8)
        tk.Label(body, text="Нужен Client ID своего Microsoft Entra-приложения с разрешением Minecraft API. Пароль вводится только на официальной странице Microsoft.", bg="#080808", fg="#777777", font=("Segoe UI", 8), wraplength=760, justify="left").pack(anchor="w", pady=(5, 0))
        tk.Label(body, text="ПСЕВДОНИМ В ОКНЕ ЛАУНЧЕРА (НЕ ИГРОВОЙ НИК)", bg="#080808", fg="#f5f5f5", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 3))
        self.alias = tk.StringVar(value=self.settings.get("display_alias", ""))
        tk.Entry(body, textvariable=self.alias, bg="#171717", fg="#f2f2f2", insertbackground="#e3262e", relief="flat", font=("Segoe UI", 10)).pack(fill="x", ipady=7)
        tk.Label(body, text="Настоящий ник в Minecraft берётся из подтверждённого Microsoft-профиля.", bg="#080808", fg="#777777", font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))
        box = tk.Frame(body, bg="#111111", highlightbackground="#292929", highlightthickness=1)
        box.pack(fill="x", pady=(12, 10))
        tk.Label(box, text="ПРОФИЛЬ И СЕРВЕР", bg="#111111", fg="#999999", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(8, 2))
        tk.Label(box, textvariable=self.profile_name, bg="#111111", fg="#f1f1f1", font=("Consolas", 13, "bold")).pack(anchor="w", padx=14, pady=(0, 3))
        tk.Label(box, text=f"Minecraft Java {MC_VERSION}  •  Forge {FORGE_BUILD}  •  {SERVER_HOST}:{SERVER_PORT}", bg="#111111", fg="#b7b7b7", font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(0, 8))
        self.progress = ttk.Progressbar(body, style="Red.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(0, 7))
        self.status = tk.StringVar(value="ГОТОВ К УСТАНОВКЕ")
        tk.Label(body, textvariable=self.status, bg="#080808", fg="#e3262e", font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        buttons = tk.Frame(body, bg="#080808")
        buttons.pack(fill="x", pady=(13, 0))
        self.login_button = tk.Button(buttons, text="ВОЙТИ MICROSOFT", command=self.login, bg="#2a2a2a", fg="white", activebackground="#424242", relief="flat", padx=14, pady=12, font=("Segoe UI", 9, "bold"))
        self.login_button.pack(side="left", fill="x", expand=True, padx=(0, 7))
        self.install_button = tk.Button(buttons, text="СКАЧАТЬ / УСТАНОВИТЬ", command=self.install, bg="#a9161d", fg="white", activebackground="#d71920", relief="flat", padx=14, pady=12, font=("Segoe UI", 9, "bold"))
        self.install_button.pack(side="left", fill="x", expand=True, padx=(0, 7))
        self.play_button = tk.Button(buttons, text="ИГРАТЬ НА LEDERG  ▶", command=self.play, bg="#252525", fg="white", activebackground="#414141", relief="flat", padx=14, pady=12, font=("Segoe UI", 9, "bold"))
        self.play_button.pack(side="left", fill="x", expand=True)
        tk.Label(body, text="Minecraft загружается официально; ZIP содержит только моды и конфиги. Offline-вход и подмена ника не используются.", bg="#080808", fg="#707070", font=("Segoe UI", 8), wraplength=760, justify="left").pack(anchor="w", pady=(9, 0))

    def save_settings(self):
        url = self.pack_url.get().strip()
        if url:
            try:
                drive_download_url(url)
            except ValueError as exc:
                messagebox.showerror("Google Drive", str(exc), parent=self.root)
                return False
        client_id = self.client_id.get().strip()
        if client_id and not re.fullmatch(r"[0-9A-Fa-f-]{36}", client_id):
            messagebox.showerror("Microsoft Client ID", "Client ID должен быть GUID из Overview приложения Microsoft Entra.", parent=self.root)
            return False
        alias = self.alias.get().strip()
        if len(alias) > 24:
            messagebox.showerror("Псевдоним", "Псевдоним лаунчера не должен превышать 24 символа.", parent=self.root)
            return False
        self.settings.update({"modpack_url": url, "microsoft_client_id": client_id, "display_alias": alias})
        save_settings(self.settings)
        self.root.title("LEDERG STALKER | " + (alias or "Independent Launcher"))
        self.status.set("НАСТРОЙКИ СОХРАНЕНЫ ЛОКАЛЬНО")
        return True

    def later(self, function, *args):
        self.root.after(0, lambda: function(*args))

    def set_status(self, value):
        self.later(self.status.set, value)

    def update_progress(self, value, reset_max=False):
        def update():
            if reset_max:
                self.progress.configure(maximum=max(1, value))
            else:
                maximum = float(self.progress.cget("maximum") or 100)
                if value > maximum:
                    self.progress.configure(maximum=value)
                self.progress.configure(value=value)
        self.root.after(0, update)

    def set_busy(self, flag):
        self.busy = flag
        state = "disabled" if flag else "normal"
        self.login_button.configure(state=state)
        self.install_button.configure(state=state)
        self.play_button.configure(state=state)

    def run_worker(self, function, *args):
        if self.busy:
            return
        self.set_busy(True)
        self.progress.configure(value=0, maximum=100)
        threading.Thread(target=self._worker_wrapper, args=(function, args), daemon=True).start()

    def _worker_wrapper(self, function, args):
        try:
            function(*args)
        except Exception as exc:
            self.later(messagebox.showerror, "LEDERG Launcher", str(exc), self.root)
            self.set_status("ОШИБКА")
        finally:
            self.later(self.set_busy, False)

    def login(self):
        if not self.save_settings():
            return
        client_id = self.client_id.get().strip()
        if not client_id:
            messagebox.showinfo(
                "Microsoft Client ID",
                "Создай Microsoft Entra App Registration, добавь redirect URI "
                + REDIRECT_URI
                + " на платформе Mobile and desktop applications и введи Client ID. Для новых приложений требуется разрешение Minecraft API.",
                parent=self.root,
            )
            return
        self.run_worker(self.login_worker, client_id)

    def login_worker(self, client_id):
        self.set_status("ОТКРЫВАЮ ОФИЦИАЛЬНЫЙ ВХОД MICROSOFT…")
        profile = login_microsoft(client_id)
        self.later(self.profile_name.set, str(profile["name"]))
        self.set_status("ВХОД ВЫПОЛНЕН • " + str(profile["name"]))

    def install(self):
        if not self.save_settings():
            return
        url = self.pack_url.get().strip()
        if not url:
            messagebox.showinfo("Модпак", "Вставь публичную ссылку Google Drive на LEDERG-STALKER-pack.zip.", parent=self.root)
            return
        self.run_worker(self.install_worker, url)

    def install_worker(self, url):
        folder = app_dir()
        pack = folder / "LEDERG-STALKER-pack.zip"
        self.set_status("СКАЧИВАЮ МОДПАК С GOOGLE DRIVE…")
        digest = download_modpack(url, pack, lambda done, total: self.update_progress(done * 100 / total if total else 0))
        self.set_status("ZIP ПРОВЕРЕН • УСТАНАВЛИВАЮ MINECRAFT / JAVA 8")
        game_dir, version_id = ensure_game_installed(lambda value: self.set_status(str(value)), self.update_progress)
        self.set_status("РАСПАКОВЫВАЮ МОДЫ И НАСТРОЙКИ LEDERG…")
        files = extract_modpack(pack, game_dir, folder / "managed-pack-files.json")
        (folder / "installed.json").write_text(json.dumps({"pack_sha256": digest, "version_id": version_id, "server": f"{SERVER_HOST}:{SERVER_PORT}", "files": len(files)}, indent=2), encoding="utf-8")
        self.update_progress(100)
        self.set_status("КЛИЕНТ ГОТОВ • FORGE 1.12.2")
        self.later(messagebox.showinfo, "LEDERG готов", "Minecraft, Forge и модпак установлены. Войди в Microsoft, затем нажми «Играть на LEDERG».", self.root)

    def play(self):
        if not self.save_settings():
            return
        if not load_account_record():
            messagebox.showinfo("Вход", "Сначала нажми «Войти Microsoft». Нужен аккаунт с Minecraft: Java Edition.", parent=self.root)
            return
        if not self.pack_url.get().strip():
            messagebox.showinfo("Модпак", "Вставь публичную ссылку на LEDERG-STALKER-pack.zip в Google Drive.", parent=self.root)
            return
        self.run_worker(self.play_worker, self.client_id.get().strip(), self.pack_url.get().strip())

    def play_worker(self, client_id, url):
        if not client_id:
            raise ValueError("Введи Microsoft Client ID перед входом.")
        profile = refresh_account(client_id)
        self.later(self.profile_name.set, str(profile["name"]))
        folder = app_dir()
        pack = folder / "LEDERG-STALKER-pack.zip"
        installed_path = folder / "installed.json"
        if not pack.is_file() or not installed_path.is_file():
            self.install_worker(url)
        self.set_status("ПРОВЕРЯЮ MINECRAFT / FORGE…")
        game_dir, version_id = ensure_game_installed(lambda value: self.set_status(str(value)), self.update_progress)
        enforce_server_menu(game_dir / "config" / "CustomMainMenu" / "mainmenu.json")
        java_exe = ensure_java8()
        options = build_launch_options(profile, java_exe, game_dir)
        self.set_status("ЗАПУСКАЮ MINECRAFT И ПОДКЛЮЧАЮ К LEDERG…")
        command = mclib.command.get_minecraft_command(version_id, str(game_dir), options)
        subprocess.Popen(command, cwd=str(game_dir), close_fds=True)
        self.set_status("MINECRAFT ЗАПУЩЕН • " + str(profile["name"]))

    def run(self):
        self.root.mainloop()

def main():
    if os.name != "nt":
        raise SystemExit("LEDERG Launcher предназначен для Windows.")
    LauncherUI().run()

if __name__ == "__main__":
    main()
