; versão vem do core.__version__ via build.bat (/DAppVer=...)
#ifndef AppVer
  #define AppVer "dev"
#endif
[Setup]
AppName=LuckHarness
AppVersion={#AppVer}
AppPublisher=LuckHarness
DefaultDirName={autopf}\luckHarness
DefaultGroupName=LuckHarness
OutputDir=dist
OutputBaseFilename=luckHarness-Setup
SetupIconFile=avatar.ico
UninstallDisplayIcon={app}\luckHarness.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
WizardStyle=modern

[Languages]
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"

[Files]
Source: "dist\luckHarness.exe"; DestDir: "{app}"

[Icons]
Name: "{group}\LuckHarness"; Filename: "{app}\luckHarness.exe"
Name: "{group}\Desinstalar LuckHarness"; Filename: "{uninstallexe}"
Name: "{autodesktop}\LuckHarness"; Filename: "{app}\luckHarness.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\luckHarness.exe"; Description: "Abrir LuckHarness"; Flags: nowait postinstall skipifsilent
