; SteadyOps Inno Setup script
; Build onedir first: .\scripts\build.ps1
; Then: .\scripts\build.ps1 -Installer
; Or manually: ISCC.exe /DMyAppVersion=0.2.0 installers\SteadyOps.iss
;
; Default install: %LOCALAPPDATA%\Programs\SteadyOps (no admin)
; User data stays in %APPDATA%\SteadyOps (not touched by install/uninstall)

#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif

#define MyAppName "SteadyOps"
#define MyAppPublisher "SteadyOps"
#define MyAppExeName "SteadyOps.exe"
#define MyAppId "{{A8F3C2E1-9B4D-4F6A-8C1E-2D7B5A9E0F31}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install — avoids UAC; aligns with custom updater (scheme B)
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=SteadyOps-Setup-{#MyAppVersion}
SetupIconFile=..\agent\desktop\assets\icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; Allow reinstall / upgrade over the same directory
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
; Bundled translation (Inno Setup 6.x may not ship Chinese by default).
; Source: https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; PyInstaller onedir output
Source: "..\dist\SteadyOps\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Only remove leftover files under the install dir; never touch %APPDATA%\SteadyOps
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"
