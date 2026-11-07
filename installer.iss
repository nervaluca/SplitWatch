; FZR SDR - installazione per Windows (Inno Setup 6)
; Compilato da GitHub Actions dopo l'exe:  ISCC /DMyAppVersion=1.34 installer.iss
; Il risultato è Output\FZRSDR_Setup_<versione>.exe
;
; - installa per l'utente (nessun permesso di amministratore) in %LOCALAPPDATA%\Programs\FZR SDR;
;   chi vuole può scegliere «per tutti gli utenti» (Program Files): in quel caso impostazioni,
;   registro e licenza vanno in %APPDATA%\FZRSDR, il programma lo fa già da solo
; - copia perseususb.dll e i file .sbs del Perseus da CW Skimmer / software Perseus / HDSDR
;   (sono file di Microtelecom: non stanno nel setup, si prendono dal PC)
; - copia l'elenco dei nominativi (MASTER.SCP di N1MM o MASTER.DTA di CW Skimmer) se c'è
; - aggiornando, chiude FZR SDR e non tocca impostazioni, memorie, licenza e registrazioni
; - FZR SDR si chiamava FZR Skimmer: stesso AppId, quindi aggiorna l'installazione vecchia nella sua cartella
;   (impostazioni e licenza restano lì) e toglie FZRSkimmer.exe e i vecchi collegamenti

#ifndef MyAppVersion
  #define MyAppVersion "0.0"
#endif
#define MyAppName "FZR SDR"
#define MyAppExe "FZRSDR.exe"

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
OutputBaseFilename=FZRSDR_Setup_{#MyAppVersion}
SetupIconFile=logo.ico
; immagini della procedura guidata (logo FZR SDR): una per ogni scala dello schermo, Inno sceglie la migliore
WizardImageFile=wizard_100.bmp,wizard_150.bmp,wizard_200.bmp
WizardSmallImageFile=wizard_small_100.bmp,wizard_small_150.bmp,wizard_small_200.bmp
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
it.Avvia=Avvia FZR SDR
it.NoPerseus=Non ho trovato perseususb.dll (CW Skimmer, software Perseus o HDSDR).%n%nCon una chiavetta RTL-SDR non serve: scegli «RTL-SDR (chiavetta)» in basso in FZR SDR.%n%nSe usi il Perseus, installa il suo software oppure copia perseususb.dll e i file perseus*.sbs nella cartella del programma; poi dal menu Start: «Copia i file del Perseus».%n%nCon ExtIO, l'audio della radio o le registrazioni non servono.
en.DesktopIcon=Desktop icon
en.Leggimi=Instructions (README)
en.CopiaPerseus=Copy the Perseus files
en.Avvia=Start FZR SDR
en.NoPerseus=perseususb.dll was not found (CW Skimmer, Perseus software or HDSDR).%n%nWith an RTL-SDR dongle it is not needed: pick «RTL-SDR (dongle)» at the bottom of FZR SDR.%n%nIf you use the Perseus, install its software or copy perseususb.dll and the perseus*.sbs files into the program folder; then from the Start menu: «Copy the Perseus files».%n%nThey are not needed for ExtIO, radio audio or recordings.

[InstallDelete]
; il programma si chiamava FZR Skimmer: via l'exe e i collegamenti col nome vecchio
Type: files; Name: "{app}\FZRSkimmer.exe"
Type: files; Name: "{autodesktop}\FZR Skimmer.lnk"
Type: filesandordirs; Name: "{autoprograms}\FZR Skimmer"
; anche se la versione vecchia era installata nell'altro modo (solo per me / per tutti gli utenti):
; altrimenti resta un FZR Skimmer 1.83 avviabile da una sua icona
Type: files; Name: "{localappdata}\Programs\FZR Skimmer\FZRSkimmer.exe"
Type: files; Name: "{commonpf32}\FZR Skimmer\FZRSkimmer.exe"
Type: files; Name: "{userdesktop}\FZR Skimmer.lnk"
Type: files; Name: "{commondesktop}\FZR Skimmer.lnk"
Type: filesandordirs; Name: "{userprograms}\FZR Skimmer"
Type: filesandordirs; Name: "{commonprograms}\FZR Skimmer"

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "LEGGIMI.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "README_EN.md"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "prepara_cartella.bat"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "logo.png"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\rtl\*"; DestDir: "{app}\rtl"; Flags: ignoreversion skipifsourcedoesntexist recursesubdirs
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
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM rtl_tcp.exe"; Flags: runhidden; RunOnceId: "ChiudiRTL"

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

{ aggiornando: FZR SDR va chiuso (l'exe "a file unico" sono due processi) }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#MyAppExe}', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM FZRSkimmer.exe', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM rtl_tcp.exe', '', SW_HIDE, ewWaitUntilTerminated, RC);
  Sleep(800);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not FileExists(ExpandConstant('{app}\perseususb.dll')) and not WizardSilent() then
    MsgBox(CustomMessage('NoPerseus'), mbInformation, MB_OK);
end;
