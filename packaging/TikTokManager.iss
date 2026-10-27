; Inno Setup script for TikTokManager-Setup.exe. build.py runs it after PyInstaller:
;   iscc /DAppVersion=1.3.0 /DSourceDir=dist\TikTokManager /DOutputDir=dist packaging\TikTokManager.iss
;
; Installs for the current user only (no admin prompt) into %LOCALAPPDATA%\Programs\TikTok Manager,
; so the in-app updater can replace the folder later. Your library, queues, settings and TikTok
; logins live in %APPDATA% / %LOCALAPPDATA%\TikTokManager and are never touched by installing;
; uninstalling asks whether to delete them.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\TikTokManager"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
; Never change AppId: Windows uses it to recognise the installed app for updates and uninstalling.
AppId={{70C03803-B73F-4A78-AF2E-A0653E15CF49}
AppName=TikTok Manager
AppVersion={#AppVersion}
AppVerName=TikTok Manager {#AppVersion}
AppPublisher=iSlipperyX
AppPublisherURL=https://github.com/iSlipperyX/TTUpdates
AppUpdatesURL=https://github.com/iSlipperyX/TTUpdates/releases
DefaultDirName={localappdata}\Programs\TikTok Manager
DefaultGroupName=TikTok Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
UsePreviousAppDir=yes
OutputDir={#OutputDir}
OutputBaseFilename=TikTokManager-Setup
SetupIconFile=tiktokmanager.ico
UninstallDisplayIcon={app}\TikTokManager.exe
UninstallDisplayName=TikTok Manager
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Close a running TikTok Manager (it saves on exit) before replacing its files.
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; A clean folder each time, so files a newer version dropped don't linger.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\TikTok Manager"; Filename: "{app}\TikTokManager.exe"
Name: "{autodesktop}\TikTok Manager"; Filename: "{app}\TikTokManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TikTokManager.exe"; Description: "Start TikTok Manager"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Left behind by the in-app updater.
Type: filesandordirs; Name: "{app}.old"

[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if SuppressibleMsgBox('Also delete your TikTok Manager data from this PC?' + #13#10#13#10 +
              'That is your video library, queues, schedules, settings, stats and the TikTok logins kept by ' +
              'the app. Your videos themselves and your TikTok accounts are not touched.' + #13#10#13#10 +
              'Choose No to keep it for a later reinstall.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    begin
      DelTree(ExpandConstant('{userappdata}\TikTokManager'), True, True, True);
      DelTree(ExpandConstant('{localappdata}\TikTokManager'), True, True, True);
    end;
  end;
end;
