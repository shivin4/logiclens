; LogicLens Windows installer (Inno Setup 6+)
; --------------------------------------------
; Prerequisites:
;   1. From repo root: pip install pyinstaller && pyinstaller packaging/logiclens.spec
;   2. Confirm dist\LogicLens\LogicLens.exe exists
;   3. Open this script in Inno Setup → Build → Compile
;
; Output: dist_installer\LogicLens-Setup-x.y.z.exe
;
; Before shipping: replace AppId with a NEW unique GUID (Tools → Generate GUID in Inno,
; or: https://www.guidgen.com — keep braces in the AppId line).

#define MyAppName "LogicLens"
#define MyAppVersion "1.1.0"
#define MyAppPublisher "LogicLens"
#define MyAppExeName "LogicLens.exe"
; Public homepage / source (shown in Programs and Features)
#define MyAppURL "https://github.com/shivin4/logiclens"
; Used for Windows taskbar / jump lists (stable string per product line)
#define MyAppUserModelId "LogicLens.LogicLens.Desktop.1"

[Setup]
AppId={{463BB9A9-ECCF-4F96-8C83-FC63782333A4}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Per-user install (no admin UAC): Start Menu + search index friendly
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=..\dist_installer
OutputBaseFilename=LogicLens-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
CloseApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=LogicLens Code Dependency Analyzer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
SetupIconFile=..\logo\logo.ico
; Code signing (optional — see packaging/SIGNING.md):
; SignTool=signtool
; SignedUninstaller=yes
; In [Files] / custom steps, use SignTool to sign LogicLens.exe before packaging, or sign the final Setup output.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\LogicLens\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Start Menu — Windows Search indexes this folder
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "{#MyAppUserModelId}"; Comment: "Visualize code dependencies and run AI-assisted analysis."
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; AppUserModelID: "{#MyAppUserModelId}"; Comment: "LogicLens — Code Dependency Analyzer"

[Registry]
; Register with "App Paths" so Run dialog / some search surfaces can resolve LogicLens.exe
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
