# 重建 _vendored（pip --target 方式装出的 42 个包，不入库）
# 用法: pwsh -File scripts/bootstrap-vendor.ps1
#   可选 -Python "C:\Program Files\Python312\python.exe"  指定 3.12 解释器
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "py -3.12",
    [string]$Requirements = "requirements-vendor.txt"
)
$ErrorActionPreference = "Stop"
$req    = Join-Path $Root $Requirements
$target = Join-Path $Root "_vendored"
if (-not (Test-Path $req)) { throw "缺少 $req" }
New-Item -ItemType Directory -Force -Path $target | Out-Null

$parts = $Python.Split(" ")
$exe   = $parts[0]
$args0 = @()
if ($parts.Count -gt 1) { $args0 = $parts[1..($parts.Count - 1)] }
$args0 += @("-m", "pip", "install", "--upgrade", "--target", $target, "-r", $req)

Write-Host "[bootstrap] $exe $($args0 -join ' ')"
& $exe @args0
if ($LASTEXITCODE -ne 0) { throw "pip 安装失败（退出码 $LASTEXITCODE）" }
Write-Host "[bootstrap] _vendored 重建完成: $target"
