# Building LogicLens-Setup.exe (Windows)

## 1. Python environment

Use Python 3.10+ and install project dependencies:

```powershell
cd path\to\LogicLens-Code-Dependency-Analyzer-main
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pyinstaller
```

## 2. PyInstaller (on-folder build)

From the **repository root**:

```powershell
# If PyInstaller says dist\LogicLens is not empty, remove it first:
Remove-Item -Recurse -Force dist\LogicLens -ErrorAction SilentlyContinue
pyinstaller packaging\logiclens.spec --noconfirm
```

Confirm `dist\LogicLens\LogicLens.exe` runs (double-click or run from that folder).

## 3. Inno Setup

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php).
2. Open `packaging\installer.iss` in Inno Setup Compiler.
3. **Before first public release:** generate a **new** `AppId` GUID (Inno: *Tools → Generate GUID*) and replace the `AppId={{...}}` line in `installer.iss`.
4. Update `#define MyAppVersion`, `MyAppURL`, and optionally `MyAppPublisher`.
5. Build → Compile.

Output: `dist_installer\LogicLens-Setup-x.y.z.exe` (version must match `#define MyAppVersion` in `installer.iss` and `logiclens/version.py`).

### Inno compile error: `The system cannot find the path specified` (during compression)

Often **Windows MAX_PATH (~260 characters)** with a **long clone directory** (e.g. `...\LogicLens-Code-Dependency-Analyzer-main\LogicLens-Code-Dependency-Analyzer-main\...`) plus deep bundled assets. The PyInstaller spec **drops `litellm/proxy/`** data (unused by the desktop CrewAI client) to shorten paths; still, prefer a **short repo root** such as `C:\dev\logiclens` for builds. Optional: enable **long paths** in Windows (Group Policy *Enable Win32 long paths* or registry `LongPathsEnabled`).

### Inno compile error: `EndUpdateResource failed` (110)

Inno embeds icons and version info into `Setup.exe` at the end of the build. **Windows Defender (or other AV)** often scans or locks that file immediately, which produces this error.

Try, in order:

1. **Delete** any existing `dist_installer\LogicLens-Setup-*.exe` (and `unins000.exe` if present from a test install), then compile again.
2. **Exclude** the repo folder or at least `dist_installer` from real-time scanning (Windows Security → Virus & threat protection → Manage settings → Exclusions).
3. **Close File Explorer** windows that are open inside `dist_installer` (preview pane can hold a lock).
4. **Compile from a short path** outside Desktop/Downloads if needed: in `installer.iss`, change the `[Setup]` line `OutputDir=..\dist_installer` to something like `OutputDir=C:\dev\LogicLensInstallerOut` (create the folder first).

## 3b. Code signing (recommended for releases)

See **[SIGNING.md](SIGNING.md)** — sign `dist\LogicLens\LogicLens.exe` before compiling Inno (or sign both the unpacked exe and the final setup `.exe`).

## 4. Ship the installer

- Upload the `.exe` to **GitHub Releases** (or your CDN).
- Set the download URL on your landing page (`docs/index.html` → `INSTALLER_URL` in the script block).

## Search in Windows

The installer:

- Adds **Start Menu** shortcuts (indexed by Windows Search).
- Registers **HKCU\...\App Paths\LogicLens.exe** for launcher resolution.
- Uses **per-user** install under `%LocalAppData%\Programs\LogicLens` (no admin by default).

If the app does not appear immediately in search, open the Start Menu once or sign out/in so the index refreshes.
