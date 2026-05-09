# Code signing (Windows) — SmartScreen & trust

Unsigned `LogicLens.exe` and `LogicLens-Setup-*.exe` often trigger **Windows SmartScreen** (“Windows protected your PC”). Signing with a **trusted code-signing certificate** reduces warnings after the file has enough reputation.

## What you need

- A **code signing certificate** (Authenticode), typically from a commercial CA or your org’s IT.
- **Windows SDK** (includes `signtool.exe`) or Visual Studio “Desktop development with C++” workload.

Locate `signtool`:

`"C:\Program Files (x86)\Windows Kits\10\bin\<version>\x64\signtool.exe"`

## Sign the application executable (after PyInstaller)

From the repo root, after `pyinstaller packaging\logiclens.spec`:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 <THUMBPRINT_OF_CERT> "dist\LogicLens\LogicLens.exe"
```

Or with a PFX file:

```powershell
signtool sign /fd SHA256 /f path\to\cert.pfx /p <password> /tr http://timestamp.digicert.com /td SHA256 "dist\LogicLens\LogicLens.exe"
```

Use your CA’s recommended **timestamp** URL.

## Sign the Inno Setup output

After compiling `installer.iss`, sign the generated setup:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /sha1 <THUMBPRINT> "dist_installer\LogicLens-Setup-1.1.0.exe"
```

## Inno Setup integration

You can configure **Sign Tool** in Inno’s IDE (*Tools → Configure Sign Tools*) and use the `SignTool` directive in `installer.iss`. See [Inno Setup: Signing](https://jrsoftware.org/ishelp/index.php?topic=signing).

## EV certificates

**Extended Validation (EV)** hardware tokens often give **immediate** SmartScreen trust compared to standard OV certs. Budget and process vary.

## This repository

The checked-in `installer.iss` does **not** include real certificate paths. Maintain signing steps in your **private release checklist** or CI secrets.
