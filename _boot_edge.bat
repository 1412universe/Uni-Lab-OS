@echo off
call C:\ProgramData\miniforge3\Scripts\activate.bat unilab
cd /d D:\Download\UniLabOS-new
python -m unilabos.app.main workspace start --workspace "d:\Download\UniLabOS-new\unilabos\devices\workstation\GN\gn-lab" --component os --wait 300 --json
echo EXIT_CODE=%ERRORLEVEL%
