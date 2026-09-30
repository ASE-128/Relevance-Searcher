@echo off
setlocal

rem ---- resolve project root from this script location (CWD-independent) ----
set "BASE=%~dp0"

rem ---- 1) prefer project-local runtime\python.exe ----
set "PY=%BASE%runtime\python.exe"
if exist "%PY%" goto :use_py

rem ---- 2) fall back to python on PATH ----
set "PY=python"
where "%PY%" >nul 2>nul
if errorlevel 1 (
    echo [launcher] ERROR: no usable python found. Need CPython 3.12. 1>&2
    echo [launcher] Install Python 3.12, or place a portable python at runtime\python.exe. 1>&2
    exit /b 1
)

:use_py
rem ---- version guard: must be 3.12 (_vendored deps are cp312 builds) ----
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3,12) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [launcher] WARNING: "%PY%" is not CPython 3.12; _vendored deps may fail to load. 1>&2
)

rem ---- run MCP stdio server ----
rem SEARXNG_URL defaults to http://127.0.0.1:8888, overridable via env.
"%PY%" "%BASE%server.py"
exit /b %errorlevel%
