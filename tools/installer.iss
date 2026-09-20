#ifndef AppVersion
  #define AppVersion "0.1.5"
#endif
#define ProductName "克克咪 火花搭子"

[Setup]
AppId={{B7782606-4C63-4AFD-B8C2-68A955E05BF8}
AppName={#ProductName}
AppVersion={#AppVersion}
AppPublisher=Spark Mate contributors
DefaultDirName={localappdata}\Programs\SparkMate
DefaultGroupName={#ProductName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\{#AppVersion}
OutputBaseFilename={#ProductName}-{#AppVersion}-windows-x64-setup
SetupIconFile=..\src\spark_mate\assets\icon.ico
UninstallDisplayIcon={app}\{#ProductName}.exe
LicenseFile=..\LICENSE
Compression=lzma2/fast
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl,Chinese.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Files]
Source: "..\dist\{#AppVersion}\{#ProductName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#ProductName}"; Filename: "{app}\{#ProductName}.exe"
Name: "{autodesktop}\{#ProductName}"; Filename: "{app}\{#ProductName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ProductName}.exe"; Description: "打开{#ProductName}"; Flags: nowait postinstall skipifsilent
