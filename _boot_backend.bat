@echo off
call C:\ProgramData\miniforge3\Scripts\activate.bat unilab
cd /d D:\Download\UniLabOS-new
python -m unilabos.app.main workspace start --workspace "d:\Download\UniLabOS-new\unilabos\devices\workstation\GN\gn-lab" --graph "d:\Download\UniLabOS-new\unilabos\devices\workstation\GN\gn-lab\deployment\graphs\gn-station.json" --runtime-mode normal --startup-mode develop --component backend --wait 300 --json
echo EXIT_CODE=%ERRORLEVEL%
