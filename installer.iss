; FZR Skimmer - installazione per Windows (Inno Setup 6)
; Compilato da GitHub Actions dopo l'exe:  ISCC /DMyAppVersion=1.34 installer.iss
; Il risultato è Output\FZRSkimmer_Setup_<versione>.exe
;
; - installa per l'utente (nessun permesso di amministratore) in %LOCALAPPDATA%\Programs\FZR Skimmer;
;   chi vuole può scegliere «per tutti gli utenti» (Program Files): in quel caso impostazioni,
;   registro e licenza vanno in %APPDATA%\FZRSkimmer, il programma lo fa già da solo
; - copia perseususb.dll e i file .sbs del Perseus da CW Skimmer / software Perseus / HDSDR
;   (sono file di Microtelecom: non stanno nel setup, si prendono dal PC)
; - copia l'elenco dei nominativi (MASTER.SCP di N1MM o MASTER.DTA di CW Skimmer) se c'è
; - aggiornando, chiude FZR Skimmer e non tocca impostazioni, memorie, licenza e registrazioni

#ifndef MyAppVersion
  #define MyAppVersion "0.0"
#endif
#define MyAppName "FZR Skimmer"
#define MyAppExe "FZRSkimmer.exe"

[Setup]
AppId={{1CD37371-78D0-5699-9C91-B81A21FB4C3A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=IW1FZR
AppPublisherURL=https://www.iw1fzr.it
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog
OutputDir=Output
OutputBaseFilename=FZRSkimmer_Setup_{#MyAppVersion}
SetupIconFile=logo.ico
UninstallDisplayIcon={app}\{#MyAppExe}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=auto

[Languages]
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
it.DesktopIcon=Icona sul desktop
it.Leggimi=Istruzioni (LEGGIMI)
it.CopiaPerseus=Copia i file del Perseus
it.Avvia=Avvia FZR Skimmer
it.NoPerseus=Non ho trovato perseususb.dll (CW Skimmer, software Perseus o HDSDR).%n%nSe usi il Perseus, installa il suo software oppure copia perseususb.dll e i file perseus*.sbs nella cartella del programma; poi dal menu Start: «Copia i file del Perseus».%n%nCon ExtIO, l'audio della radio o le registrazioni non servono.
en.DesktopIcon=Desktop icon
en.Leggimi=Instructions (README)
en.CopiaPerseus=Copy the Perseus files
en.Avvia=Start FZR Skimmer
en.NoPerseus=perseususb.dll was not found (CW Skimmer, Perseus software or HDSDR).%n%nIf you use the Perseus, install its software or copy perseususb.dll and the perseus*.sbs files into the program folder; then from the Start menu: «Copy the Perseus files».%n%nThey are not needed for ExtIO, radio audio or recordings.

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "LEGGIMI.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README_EN.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "prepara_cartella.bat"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "logo.png"; DestDir: "{app}"; Flags: ignoreversion
; file del Perseus e nominativi: presi dal PC durante l'installazione (external)
Source: "{code:PerseusDir}\perseususb.dll"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist ignoreversion
Source: "{code:PerseusDir}\perseus*.sbs"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist ignoreversion
Source: "{userdocs}\N1MM Logger+\SupportFiles\MASTER.SCP"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist
Source: "{code:PerseusDir}\MASTER.DTA"; DestDir: "{app}"; Flags: external skipifsourcedoesntexist onlyifdoesntexist; Check: NeedMasterDta

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:Leggimi}"; Filename: "{app}\LEGGIMI.md"
Name: "{group}\{cm:CopiaPerseus}"; Filename: "{app}\prepara_cartella.bat"; WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:Avvia}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM {#MyAppExe}"; Flags: runhidden; RunOnceId: "ChiudiFZR"

[Code]
var
  PDir: String;

function FindPerseus(): String;
var
  C: array of String;
  I: Integer;
begin
  Result := '';
  SetArrayLength(C, 7);
  C[0] := ExpandConstant('{commonpf32}\Afreet\CwSkimmer');
  C[1] := ExpandConstant('{commonpf}\Afreet\CwSkimmer');
  C[2] := ExpandConstant('{commonpf32}\Afreet\SkimSrv');
  C[3] := ExpandConstant('{commonpf32}\Microtelecom\Perseus');
  C[4] := ExpandConstant('{commonpf32}\Microtelecom');
  C[5] := ExpandConstant('{commonpf32}\HDSDR');
  C[6] := ExpandConstant('{commonpf}\HDSDR');
  for I := 0 to GetArrayLength(C) - 1 do
    if FileExists(C[I] + '\perseususb.dll') then
    begin
      Result := C[I];
      Exit;
    end;
end;

function PerseusDir(Param: String): String;
begin
  if PDir = '' then
    PDir := FindPerseus();
  if PDir = '' then
    Result := ExpandConstant('{tmp}')      { non esiste nulla da copiare: skipifsourcedoesntexist }
  else
    Result := PDir;
end;

function NeedMasterDta(): Boolean;
begin
  Result := not FileExists(ExpandConstant('{userdocs}\N1MM Logger+\SupportFiles\MASTER.SCP'))
            and not FileExists(ExpandConstant('{app}\MASTER.SCP'));
end;

{ aggiornando: FZR Skimmer va chiuso (l'exe "a file unico" sono due processi) }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyAppExe}', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Sleep(800);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not FileExists(ExpandConstant('{app}\perseususb.dll')) and not WizardSilent() then
    MsgBox(CustomMessage('NoPerseus'), mbInformation, MB_OK);
end;
