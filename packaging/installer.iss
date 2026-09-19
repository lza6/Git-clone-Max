; G49-5 Inno Setup 安装器脚本（可选产物，不替代便携版）
; 编译：iscc packaging/installer.iss   （Inno Setup 6 命令行）
; 产物：dist\installer\Git-clone-Max-setup-7.9.0.exe
; 说明：默认安装到 Program Files；数据目录默认走 %APPDATA%/Git-clone-Max
;       （G49-2 非便携语义），卸载不清数据，重装/升级无感。
; 需要先构建 dist\Git-clone-Max.exe（见 README 打包段）。

#define MyAppName "Git-clone-Max"
#define MyAppVersion "8.0.1"
#define MyAppExeName "Git-clone-Max.exe"
#define MyAppPublisher "lza6"
#define MyAppURL "https://github.com/lza6/Git-clone-Max"
; PowerShell 生成 GUID： [guid]::NewGuid()
#define MyAppId "B7E9C1A4-3F2D-4E5A-9B8C-1D2E3F4A5B6C"

[Setup]
AppId={{B7E9C1A4-3F2D-4E5A-9B8C-1D2E3F4A5B6C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=Git-clone-Max-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
; 中文向导集成（可选）：从 https://jrsoftware.org/files/istrans/ 下载
;   ChineseSimplified.isl 放入 <Inno>/Languages/ 后取消下行注释即可：
; Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Git-clone-Max.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; ---- 可选文件关联（.gitmax 任务清单）----
[Registry]
Root: HKCU; Subkey: "Software\Classes\.gitmax"; ValueType: string; ValueName: ""; ValueData: "GitCloneMaxList"; Flags: uninsdeletevalue
Root: HKCU; Subkey: "Software\Classes\GitCloneMaxList\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletevalue

[UninstallDelete]
; 卸载不删 %APPDATA%/Git-clone-Max（数据/凭据/日志属于用户数据）
Type: filesandordirs; Name: "{app}\__pycache__"
