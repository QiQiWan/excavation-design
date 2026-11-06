@echo off
setlocal
set "SCRIPT=%~dp0services\api\app\engineering\single_braced_pile\assurance\dossier_signature.py"
if not exist "%SCRIPT%" (
  echo [P4] Missing dossier signature tool in this installation.
  exit /b 2
)
python "%SCRIPT%" %*
exit /b %ERRORLEVEL%
