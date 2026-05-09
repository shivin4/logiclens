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
pyinstaller packaging\logiclens.spec
```

Confirm `dist\LogicLens\LogicLens.exe` runs (double-click or run from that folder).

## 3. Inno Setup

1. Install [Inno Setup 6](https://jrsoftware.org/isdl.php).
2. Open `packaging\installer.iss` in Inno Setup Compiler.
3. **Before first public release:** generate a **new** `AppId` GUID (Inno: *Tools → Generate GUID*) and replace the `AppId={{...}}` line in `installer.iss`.
4. Update `#define MyAppVersion`, `MyAppURL`, and optionally `MyAppPublisher`.
5. Build → Compile.

Output: `dist_installer\LogicLens-Setup-x.y.z.exe` (version must match `#define MyAppVersion` in `installer.iss` and `logiclens/version.py`).

## 3b. Code signing (recommended for releases)

See **[SIGNING.md](SIGNING.md)** — sign `dist\LogicLens\LogicLens.exe` before compiling Inno (or sign both the unpacked exe and the final setup `.exe`).

## 4. Ship the installer

- Upload the `.exe` to **GitHub Releases** (or your CDN).
- Set the download URL on your landing page (`landing/index.html` → `INSTALLER_URL` in the script block).

## Search in Windows

The installer:

- Adds **Start Menu** shortcuts (indexed by Windows Search).
- Registers **HKCU\...\App Paths\LogicLens.exe** for launcher resolution.
- Uses **per-user** install under `%LocalAppData%\Programs\LogicLens` (no admin by default).

If the app does not appear immediately in search, open the Start Menu once or sign out/in so the index refreshes.
