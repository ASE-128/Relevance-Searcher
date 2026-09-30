# 把本仓库对 SearXNGforWindows 的本地定制重新应用一遍（幂等，可重复执行）
#   - config/settings.yml      整份来自 overlays/searxng/config/settings.yml
#   - 引擎/BAT 等改动          来自 overlays/searxng/patches/searxng-local-changes.patch
# 用法: pwsh -File scripts/apply-searxng-overlay.ps1
param(
    [string]$Root = (Split-Path -Parent $PSScriptRoot),
    [string]$SearxDir = "SearXNGforWindows"
)
$ErrorActionPreference = "Stop"
$searx = Join-Path $Root $SearxDir
if (-not (Test-Path $searx)) {
    throw "找不到 $searx —— 请先执行 git submodule update --init --recursive"
}

$settings       = Join-Path $Root "overlays\searxng\config\settings.yml"
$patch          = Join-Path $Root "overlays\searxng\patches\searxng-local-changes.patch"
$targetSettings = Join-Path $searx "config\settings.yml"

if (Test-Path $settings) {
    $same = (Test-Path $targetSettings) -and
            ((Get-FileHash $settings).Hash -eq (Get-FileHash $targetSettings).Hash)
    if ($same) {
        Write-Host "[overlay] settings.yml 已是最新"
    } else {
        Copy-Item $settings $targetSettings -Force
        Write-Host "[overlay] settings.yml 已应用"
    }
}

if (Test-Path $patch) {
    # --ignore-whitespace: 该 submodule 的 core.autocrlf=true，工作区是 CRLF 而补丁上下文是 LF
    git -C $searx apply --check --ignore-whitespace $patch 2>$null
    if ($LASTEXITCODE -eq 0) {
        git -C $searx apply --ignore-whitespace $patch
        Write-Host "[overlay] 引擎补丁已应用"
    } else {
        git -C $searx apply --check --reverse --ignore-whitespace $patch 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[overlay] 引擎补丁此前已应用，无需重复"
        } else {
            Write-Host "[overlay] 引擎补丁无法应用（与上游版本冲突或工作区已被改动），请人工确认"
        }
    }
}
