import json
import zipfile
from pathlib import Path
import pytest
from launcher import (
    drive_file_id, drive_download_url, validate_modpack, extract_modpack,
    enforce_server_menu, build_launch_options, SERVER_HOST, SERVER_PORT
)

MENU = {"buttons": {
    "singleplayer": {"action": {"type": "openGui", "gui": "singleplayer"}},
    "multiplayer": {"action": {"type": "openGui", "gui": "multiplayer"}},
    "mods": {"action": {"type": "openGui", "gui": "mods"}},
}}

def make_pack(path: Path, extra=None):
    files = {
        "mods/CustomMainMenu_MC1.12.2_2.0.8.jar": b"unit-test-fixture",
        "mods/SovietEra-0.6-1.12.2.jar": b"unit-test-fixture",
        "config/CustomMainMenu/mainmenu.json": json.dumps(MENU).encode(),
        "LEDERG-MODPACK-README.txt": b"fixture",
    }
    if extra:
        files.update(extra)
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path

def test_drive_share_url():
    url = "https://drive.google.com/file/d/Abc_123-DEF/view?usp=sharing"
    assert drive_file_id(url) == "Abc_123-DEF"
    assert drive_download_url(url) == "https://drive.google.com/uc?export=download&id=Abc_123-DEF"

def test_drive_resource_key_is_preserved():
    url = "https://drive.google.com/file/d/abcDEF_123/view?resourcekey=rKey"
    assert drive_download_url(url).endswith("&resourcekey=rKey")

def test_folder_link_rejected():
    with pytest.raises(ValueError):
        drive_download_url("https://drive.google.com/drive/folders/abc123")

def test_other_hosts_rejected():
    with pytest.raises(ValueError):
        drive_download_url("https://example.org/client.zip")

def test_valid_modpack():
    names = validate_modpack(make_pack(Path(__import__("tempfile").mktemp(suffix=".zip"))))
    assert "mods/SovietEra-0.6-1.12.2.jar" in names

def test_path_traversal_rejected(tmp_path):
    archive = make_pack(tmp_path / "unsafe.zip", {"../outside.txt": b"bad"})
    with pytest.raises(ValueError, match="Небезопасный путь"):
        validate_modpack(archive)

def test_game_binaries_forbidden_in_modpack(tmp_path):
    archive = make_pack(tmp_path / "wrong.zip", {"versions/1.12.2/1.12.2.jar": b"blocked"})
    with pytest.raises(ValueError, match="файлы Minecraft"):
        validate_modpack(archive)

def test_tlauncher_files_forbidden(tmp_path):
    archive = make_pack(tmp_path / "wrong.zip", {"TLauncher.exe": b"blocked"})
    with pytest.raises(ValueError, match="TLauncher"):
        validate_modpack(archive)

def test_pack_extracts_and_menu_locks_to_server(tmp_path):
    archive = make_pack(tmp_path / "pack.zip")
    game = tmp_path / "game"
    manifest = tmp_path / "managed.json"
    extract_modpack(archive, game, manifest)
    menu = json.loads((game / "config/CustomMainMenu/mainmenu.json").read_text(encoding="utf-8"))
    assert not {"singleplayer", "multiplayer", "realms"} & set(menu["buttons"])
    assert menu["buttons"]["lederg_join"]["action"]["ip"] == f"{SERVER_HOST}:{SERVER_PORT}"
    assert (game / "mods/SovietEra-0.6-1.12.2.jar").is_file()
    assert json.loads(manifest.read_text(encoding="utf-8"))["files"]

def test_menu_enforcement_removes_other_modes(tmp_path):
    path = tmp_path / "menu.json"
    path.write_text(json.dumps(MENU), encoding="utf-8")
    enforce_server_menu(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert not {"singleplayer", "multiplayer", "realms"} & set(data["buttons"])
    assert data["buttons"]["lederg_join"]["action"]["type"] == "connectToServer"

def test_launch_uses_authenticated_account_not_display_alias(tmp_path):
    profile = {"name": "LicensedPlayer", "id": "1234", "access_token": "fake-test-token"}
    options = build_launch_options(profile, tmp_path / "javaw.exe", tmp_path / "game")
    assert options["username"] == "LicensedPlayer"
    assert options["uuid"] == "1234"
    assert options["server"] == SERVER_HOST
    assert options["port"] == str(SERVER_PORT)
