@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
title Git-clone-Max - 快速启动

rem 本脚本位于 scripts\ 目录，项目根在上一级
set "ROOT_DIR=%~dp0.."
cd /d "%ROOT_DIR%"

rem ============================================================
rem 1) 检测 git
rem ============================================================
where git >nul 2>nul
if errorlevel 1 (
    echo [ERROR] 未检测到 git，请先安装: https://git-scm.com/download/win
    pause
    exit /b 1
)

rem ============================================================
rem 2) 检测 python (py 启动器优先)
rem ============================================================
set "PY=py"
py -3 --version >nul 2>nul
if errorlevel 1 (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] 未检测到 Python 3，请先安装: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PY=python"
)

%PY% --version
echo [INFO] 使用 Python: %PY%

rem ============================================================
rem 3) 创建虚拟环境（若不存在）
rem ============================================================
set "VENV=%ROOT_DIR%\.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo [INFO] 未发现虚拟环境，正在创建 .venv ...
    %PY% -m venv "%VENV%"
    if errorlevel 1 (
        echo [ERROR] 创建虚拟环境失败
        pause
        exit /b 1
    )
)
set "VENV_PY=%VENV%\Scripts\python.exe"

rem ============================================================
rem 4) 依赖自检与安装
rem ============================================================
"%VENV_PY%" -c "import PyQt6" >nul 2>nul
if errorlevel 1 (
    echo [INFO] 首次运行，正在安装依赖 PyQt6 ...
    "%VENV_PY%" -m pip install --disable-pip-version-check -r "%ROOT_DIR%\requirements.txt"
    if errorlevel 1 (
        echo [ERROR] 依赖安装失败，请检查网络
        pause
        exit /b 1
    )
)

rem ============================================================
rem 5) 启动应用
rem ============================================================
echo [INFO] 正在启动 Git-clone-Max ...
cd /d "%ROOT_DIR%"
"%VENV_PY%" -m gcm
pause