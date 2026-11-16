; Inno Setup script for Solar Audit Platform (offline site edition).
;
; Prerequisites on the Windows build machine:
;   1. python -m venv venv && venv\Scripts\activate
;   2. pip install -r requirements.txt
;   3. pyinstaller solar_audit.spec
;      (produces dist\SolarAudit\SolarAudit.exe + bundled runtime)
;   4. Install Inno Setup (https://jrsoftware.org/isinfo.php)
;   5. Open this file in the Inno Setup Compiler and click "Compile",
;      or run from the command line:
;         "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; Output: Output\SolarAuditSetup.exe
;
; What the installer does:
;   - Installs the PyInstaller-built app under Program Files\SolarAudit
;     (read-only application code -- see paths.py).
;   - Creates a desktop shortcut and Start Menu entry.
;   - Does NOT create any database, photos, or site config at install time
;     -- those are created on first launch, written to %PROGRAMDATA%\
;     SolarAudit (a per-machine, writable location), and the /setup wizard
;     runs automatically on first launch to assign this install to a site.
;   - Does NOT require the site user to install Python, Flask, or any
;     other dependency separately -- PyInstaller already bundled the
;     Python runtime and all packages into SolarAudit.exe.

#define MyAppName "Solar Audit"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "CleanMax"
#define MyAppExeName "SolarAudit.exe"
#define MySourceDist "dist\SolarAudit"

[Setup]
AppId={{B6C1B6D1-7F2E-4E2A-9C0D-5A1E9D3F7C21}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\SolarAudit
DefaultGroupName=Solar Audit
DisableProgramGroupPage=yes
OutputDir=Output
OutputBaseFilename=SolarAuditSetup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; The app is a normal per-machine install; no elevation trickery needed
; because all *mutable* data goes to %PROGRAMDATA%, not Program Files.
PrivilegesRequired=admin
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; Everything PyInstaller produced -- the Python runtime, all dependencies,
; templates/, static/ (including vendored fonts) -- gets installed here.
; This directory is treated as read-only application code (see paths.py);
; no database or photos are ever written inside it.
Source: "{#MySourceDist}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Solar Audit"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall Solar Audit"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Solar Audit"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Solar Audit now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Uninstalling the application does NOT delete site data in %PROGRAMDATA%\
; SolarAudit -- that's a deliberate safety choice (requirement: don't
; accidentally destroy audit data). If a site is being fully decommissioned,
; that folder can be removed manually or via a separate "Remove Site Data"
; tool, not as a side effect of a normal uninstall.
