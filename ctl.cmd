@echo off
rem Short launcher for harness\ctl.py (docs/OVERSEER.md): ctl status, ctl act walk 2 3, ...
rem Python 3.13 per AGENTS.md; override with UO_PY.
if not defined UO_PY set "UO_PY=C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe"
"%UO_PY%" "%~dp0harness\ctl.py" %*
