# LEDERG STALKER — Independent Minecraft Launcher

Independent Windows launcher for Forge 1.12.2. It does not ship TLauncher files and does not distribute Minecraft game binaries.

Runtime behavior:
- Downloads the modpack ZIP directly from a public Google Drive file link.
- Validates ZIP structure and blocks path traversal, symlinks, TLauncher binaries, Minecraft libraries/assets/worlds and other non-modpack content.
- Installs official Minecraft Java Edition 1.12.2 and Forge 14.23.5.2861 in a separate folder under the local app-data directory.
- Downloads Eclipse Temurin Java 8 for this legacy Minecraft version.
- Uses Microsoft OAuth with PKCE through the system browser. The refresh token is encrypted with Windows DPAPI; no Microsoft password is collected or stored.
- On Play, starts Minecraft with LEDERG05.aternos.me:26820 as the initial server connection.
- The in-game Custom Main Menu removes Singleplayer, Multiplayer and Realms, leaving LEDERG Connect, Mods, Settings and Exit.

First-run requirements:
1. Google Drive: share LEDERG-STALKER-pack.zip as Anyone with the link — Viewer. Paste the file share URL into the launcher. The URL is blank in the public configuration because a private Drive file ID cannot be inferred safely.
2. Microsoft authentication: enter a Microsoft Entra application Client ID. Register redirect URI http://localhost:8765/callback as a Mobile and desktop application. Microsoft may require explicit permission for the app to use Minecraft API; without it, authentication fails.
3. The Microsoft account must own Minecraft: Java Edition. An arbitrary/offline name cannot replace a verified account.

Build on Windows:
- Install dependencies: py -m pip install -r requirements.txt
- Run tests: py -m pytest -q
- Build: py -m PyInstaller --clean --noconfirm --onefile --windowed --name LEDERG-Launcher --add-data "launcher_config.json;." --collect-all minecraft_launcher_lib launcher.py

GitHub Actions tests and builds the EXE, uploads an artifact and publishes a versioned GitHub Release.

Modpack contents: Soviet Era, VanillaFix, Dynamic Surroundings + OreLib, Better Foliage + Forgelin and Custom Main Menu. The ZIP contains only mod and config files. Hostile-mob spawning must be configured on Aternos; client-only mods cannot enforce that server rule.
