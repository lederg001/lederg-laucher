import json
import zipfile
from pathlib import Path
import pytest
from launcher import drive_id, direct_url, set_server, sha256, validate_zip

def test_drive_share_link_is_converted():
    url = "https://drive.google.com/file/d/Abc_123-DEF/view?usp=sharing"
    assert drive_id(url) == "Abc_123-DEF"
    assert direct_url(url) == "https://drive.google.com/uc?export=download&id=Abc_123-DEF"

def test_folder_link_is_rejected():
    with pytest.raises(ValueError):
        direct_url("https://drive.google.com/drive/folders/abc123")

def test_sha256_helper(tmp_path: Path):
    file = tmp_path / "value.txt"
    file.write_bytes(b"hello")
    assert sha256(file) == "2CF24DBA5FB0A30E26E83B2AC5B9E29E1B161E5C1FA7425E73043362938B9824"

def test_server_arguments_are_added_once(tmp_path: Path):
    profile = tmp_path / "Forge.json"
    profile.write_text(json.dumps({"minecraftArguments":"--username player --server old.host --port 25565"}), encoding="utf-8")
    set_server(profile)
    args = json.loads(profile.read_text(encoding="utf-8"))["minecraftArguments"]
    assert args.count("--server") == 1 and args.count("--port") == 1
    assert "--server LEDERG05.aternos.me --port 26820" in args
    set_server(profile)
    args = json.loads(profile.read_text(encoding="utf-8"))["minecraftArguments"]
    assert args.count("--server") == 1 and args.count("--port") == 1

def test_bad_archive_structure_is_rejected(tmp_path: Path):
    archive = tmp_path / "wrong.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("readme.txt", b"wrong")
    with pytest.raises(ValueError, match="TLauncher.exe"):
        validate_zip(archive, expected=None)

def test_path_traversal_is_rejected(tmp_path: Path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("TLauncher.exe", b"x")
        z.writestr("versions/Forge 1.12.2/Forge 1.12.2.json", b'{"minecraftArguments":"--username p"}')
        z.writestr("../outside.txt", b"bad")
    with pytest.raises(ValueError, match="Небезопасный путь"):
        validate_zip(archive, expected=None)
