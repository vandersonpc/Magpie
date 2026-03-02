; ---------------------------------------------
; Inno Setup Script for Magpie (with Uninstall)
; ---------------------------------------------

[Setup]
; Basic info
AppName=Magpie
AppVersion=1.0.1
DefaultDirName={commonpf}\Magpie
DefaultGroupName=Magpie
OutputDir=installer_output
OutputBaseFilename=Magpie_Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64

; Show uninstaller
UninstallDisplayIcon={app}\Magpie.exe
UninstallDisplayName=Magpie
Uninstallable=yes

; ---------------------------------------------
; Files to include
; ---------------------------------------------
[Files]
; Include all files from the PyInstaller dist folder
Source: "dist\Magpie\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

; ---------------------------------------------
; Shortcuts
; ---------------------------------------------
[Icons]
Name: "{group}\Magpie"; Filename: "{app}\Magpie.exe"
Name: "{userdesktop}\Magpie"; Filename: "{app}\Magpie.exe"; Tasks: desktopicon

; ---------------------------------------------
; Tasks (optional)
; ---------------------------------------------
[Tasks]
Name: desktopicon; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

; ---------------------------------------------
; Version variable injected from GitHub Actions
; ---------------------------------------------
#define MagpieVersion "1.0.0"