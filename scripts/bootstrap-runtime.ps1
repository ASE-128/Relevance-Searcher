# 重建便携 CPython 3.12.3（runtime\python，来自 python.org embeddable 包）
# run-server.cmd 在 runtime\python.exe 缺失时会回退到 PATH 上的 python 3.12，所以这一步是可选的
# 用法: pwsh -File scripts/bootstrap-runtime.ps1
param(
    [string]$Version = "3.12.3",
    [string]$Root = (Split-Path -Parent $PSScriptRoot)
)
$ErrorActionPreference = "Stop"
$target = Join-Path $Root "runtime\python"
if (Test-Path (Join-Path $target "python.exe")) {
    Write-Host "[bootstrap] runtime 已存在: $target"
    exit 0
}
$url = "https://www.python.org/ftp/python/$Version/python-$Version-embed-amd64.zip"
$tmp = Join-Path $env:TEMP "python-$Version-embed-amd64.zip"
New-Item -ItemType Directory -Force -Path $target | Out-Null
Write-Host "[bootstrap] 下载 $url"
Invoke-WebRequest -Uri $url -OutFile $tmp
Write-Host "[bootstrap] 解压到 $target"
Expand-Archive -Path $tmp -DestinationPath $target -Force
Write-Host "[bootstrap] 完成（注意：embeddable 包不含 pip，装依赖请用 scripts/bootstrap-vendor.ps1 的 -Python 参数指向本机 3.12）"
