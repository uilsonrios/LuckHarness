@echo off
rem Testa, gera o exe e o instalador com a versão de core.__version__
cd /d %~dp0
python -m pytest -q || exit /b 1
for /f %%v in ('python -c "import core; print(core.__version__)"') do set VER=%%v
pyinstaller luckHarness.spec --noconfirm || exit /b 1
"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" /Q /DAppVer=%VER% installer.iss || exit /b 1
echo LuckHarness %VER% pronto em dist\
