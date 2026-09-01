; Inno Setup Skript – erzeugt AudioHaptics-Setup.exe
; Wird automatisch von build.bat aufgerufen, wenn Inno Setup installiert ist.
#define AppName "Audio Haptics"
#define AppVersion "0.1.0"
#define AppExe "AudioHaptics.exe"

[Setup]
AppId={{7E2B9C4A-5D1F-4B7E-9A3C-AUDIOHAPTICS1}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Community-Projekt (nicht von TrueGear)
DefaultDirName={autopf}\AudioHaptics
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
OutputDir=dist
OutputBaseFilename=AudioHaptics-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\{#AppExe}
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName={#AppName}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop erstellen"; GroupDescription: "Zusätzliche Aufgaben:"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{#AppName} jetzt starten"; Flags: nowait postinstall skipifsilent

[Messages]
WelcomeLabel2=Dieses Programm macht den Bass aus Spielen, Videos und Musik auf der TrueGear ME02 spürbar.%n%nWichtig: Der TrueGear Player muss installiert sein und laufen.%n%nEs wird [name/ver] auf deinem Computer installiert.
