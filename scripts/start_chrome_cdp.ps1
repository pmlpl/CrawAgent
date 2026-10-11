# 一键拉起专用自动化 Chrome（CDP 模式，绕过 Chrome 136+/144+ 的"允许远程调试"弹窗）
#
# 原理：弹窗只针对【默认用户配置目录】。用独立 --user-data-dir + --remote-debugging-port
# 启动的 Chrome 不弹窗、不用点击，browser-harness 经 BU_CDP_URL 直连即可 100% 自动化。
#
# 用法：双击运行（或 PowerShell 执行）。幂等——端口已在监听时不再重复拉起。
# 配套：.env 里 browser-harness 的 MCP 配置已带 BU_CDP_URL=http://127.0.0.1:9333。

$ErrorActionPreference = "Stop"

$ChromePath = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$Port       = 9333
$ProfileDir = Join-Path $PSScriptRoot "..\data\browser_profile"

# 已有实例在监听则直接复用
$live = $false
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 2
    $live = $true
} catch {}

if ($live) {
    Write-Host "[OK] 专用自动化 Chrome 已在运行（端口 $Port），直接复用。"
    exit 0
}

if (-not (Test-Path $ChromePath)) {
    Write-Host "[FAIL] 未找到 Chrome：$ChromePath"
    exit 1
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
Start-Process -FilePath $ChromePath -ArgumentList @(
    "--remote-debugging-port=$Port",
    "--user-data-dir=`"$ProfileDir`"",
    "--no-first-run",
    "--no-default-browser-check"
)

# 等调试端口就绪（最多 15 秒）
$ok = $false
foreach ($i in 1..15) {
    Start-Sleep -Seconds 1
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/json/version" -UseBasicParsing -TimeoutSec 2
        $ok = $true
        break
    } catch {}
}

if ($ok) {
    Write-Host "[OK] 专用自动化 Chrome 已就绪：http://127.0.0.1:$Port（配置目录 $ProfileDir）"
} else {
    Write-Host "[FAIL] Chrome 已拉起但调试端口 $Port 未就绪，请检查防火墙/杀软对 localhost 的拦截。"
    exit 1
}
