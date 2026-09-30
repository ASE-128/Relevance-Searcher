# 把仓库内的 hooks/ 目录登记为 Git 钩子目录（core.hooksPath），一次安装、随仓库版本化
# 用法: pwsh -File scripts/install-git-hooks.ps1
param(
    [string]$Root,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }

if ($Uninstall) {
    git -C $Root config --unset core.hooksPath
    Write-Host "[hooks] 已卸载 core.hooksPath（回到 .git/hooks）"
    exit 0
}

$hooks = Join-Path $Root "hooks"
if (-not (Test-Path $hooks)) { throw "找不到 $hooks" }

git -C $Root config core.hooksPath hooks
Write-Host "[hooks] core.hooksPath = $(git -C $Root config --get core.hooksPath)"
Get-ChildItem $hooks -File | ForEach-Object {
    Write-Host "  - $($_.Name)"
}
Write-Host "[hooks] 生效范围：仅本仓库；跳过方式 git commit --no-verify / git push --no-verify"
