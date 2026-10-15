@echo off
rem FZR Skimmer - prepara la cartella con i file del Perseus
rem Copia perseususb.dll e i file .sbs da CW Skimmer (o dal software Perseus) in questa cartella.
setlocal
set "DEST=%~dp0"
set "SRC="
for %%D in ("%ProgramFiles(x86)%\Afreet\CwSkimmer" "%ProgramFiles%\Afreet\CwSkimmer" "%ProgramFiles(x86)%\Afreet\SkimSrv" "%ProgramFiles(x86)%\Microtelecom\Perseus" "%ProgramFiles(x86)%\HDSDR") do (
  if not defined SRC if exist "%%~D\perseususb.dll" set "SRC=%%~D"
)
if not defined SRC (
  echo Non trovo perseususb.dll nelle cartelle di CW Skimmer o del software Perseus.
  echo Copia a mano perseususb.dll e i file perseus*.sbs in: %DEST%
  pause
  exit /b 1
)
echo Copio i file del Perseus da: %SRC%
copy /Y "%SRC%\perseususb.dll" "%DEST%" >nul
copy /Y "%SRC%\perseus*.sbs" "%DEST%" >nul
rem elenco dei nominativi noti (facoltativo): MASTER.SCP di N1MM o MASTER.DTA di CW Skimmer
if not exist "%DEST%MASTER.SCP" if exist "%USERPROFILE%\Documents\N1MM Logger+\SupportFiles\MASTER.SCP" (
  copy /Y "%USERPROFILE%\Documents\N1MM Logger+\SupportFiles\MASTER.SCP" "%DEST%" >nul
  echo Copiato MASTER.SCP da N1MM Logger+
)
if not exist "%DEST%MASTER.SCP" if exist "%SRC%\MASTER.DTA" (
  copy /Y "%SRC%\MASTER.DTA" "%DEST%" >nul
  echo Copiato MASTER.DTA da CW Skimmer
)
if not exist "%DEST%MASTER.SCP" if not exist "%DEST%MASTER.DTA" (
  echo.
  echo Elenco nominativi non trovato: scarica MASTER.SCP da www.supercheckpartial.com
  echo e mettilo in questa cartella, per evidenziare i call riconosciuti.
)
echo Fatto. Ora la cartella %DEST% e' completa: avvia FZRSkimmer.exe
pause
