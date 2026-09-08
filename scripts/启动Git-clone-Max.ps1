# Git-clone-Max 启动脚本（PowerShell）
# 自动检测并创建虚拟环境、自检依赖、启动 GUI

$ErrorActionPreference = "Stop"
# 脚本位于 scripts\ 目录，项目根在上一级
$AppDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $AppDir

Write-Host "[INFO] Git-clone-Max 启动器 (PowerShell)" -ForegroundColor Cyan

# 1) git
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 未检测到 git，请先安装: https://git-scm.com/download/win" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

# 2) python
$py = "py"
try { & $py -3 --version | Out-Null } catch { $py = "python" }
try { & $py --version | Out-Null } catch {
    Write-Host "[ERROR] 未检测到 Python 3" -ForegroundColor Red
    Read-Host "按回车退出"; exit 1
}

# 3) venv
$venv = Join-Path $AppDir ".venv"
$venvPy = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "[INFO] 创建虚拟环境 .venv ..." -ForegroundColor Yellow
    & $py -m venv $venv
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] venv 创建失败" -ForegroundColor Red; Read-Host; exit 1 }
}

# 4) 依赖
& $venvPy -c "import PyQt6" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] 安装依赖 PyQt6 ..." -ForegroundColor Yellow
    & $venvPy -m pip install --disable-pip-version-check -r (Join-Path $AppDir "requirements.txt")
    if ($LASTEXITCODE -ne 0) { Write-Host "[ERROR] 依赖安装失败" -ForegroundColor Red; Read-Host; exit 1 }
}

# 5) 启动
Write-Host "[INFO] 启动 Git-clone-Max ..." -ForegroundColor Green
& $venvPy -m gcm
Read-Host "按回车退出"