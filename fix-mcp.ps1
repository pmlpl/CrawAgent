# ============================================================
# Fix anything-analyzer MCP service (deps + electron + native rebuild)
# Run in SYSTEM PowerShell (not Trae sandbox):
#   powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\MOM\Desktop\学习项目\CrawAgent\fix-mcp.ps1"
# ============================================================

$ErrorActionPreference = "Continue"
$TARGET_DIR = "C:\Users\MOM\Tools\anything-analyzer"

Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  anything-analyzer MCP - Repair Script" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# --- Step 1: kill stray electron procs ---
Write-Host "[1/5] Killing stray electron processes..." -ForegroundColor Yellow
$procs = Get-Process electron -ErrorAction SilentlyContinue
if ($procs) {
    $procs | Stop-Process -Force
    Write-Host "  [OK] Stopped $($procs.Count) electron process(es)" -ForegroundColor Green
} else {
    Write-Host "  [OK] No stray processes" -ForegroundColor Green
}

# --- Step 2: target dir check ---
Write-Host "[2/5] Checking target directory..." -ForegroundColor Yellow
if (-not (Test-Path $TARGET_DIR)) {
    Write-Host "  [FAIL] Directory not found: $TARGET_DIR" -ForegroundColor Red
    Write-Host "  Please git clone or extract anything-analyzer to C:\Users\MOM\Tools\"
    exit 1
}
Set-Location $TARGET_DIR
Write-Host "  [OK] Changed to $TARGET_DIR" -ForegroundColor Green

# --- Step 3: ensure full deps ---
Write-Host "[3/5] Checking dependencies..." -ForegroundColor Yellow

# check if .pnpm has packages already
$hasPnpmPkgs = (Test-Path "node_modules\.pnpm") -and ((Get-ChildItem node_modules\.pnpm -Directory -ErrorAction SilentlyContinue | Measure-Object).Count -gt 10)
$hasBetterSqlite3 = [bool](Get-ChildItem -Path "node_modules\.pnpm" -Recurse -Filter "better_sqlite3.node" -ErrorAction SilentlyContinue | Select-Object -First 1)
$hasElectronBinary = [bool](Get-ChildItem -Path "node_modules\.pnpm" -Recurse -Filter "electron.exe" -ErrorAction SilentlyContinue | Select-Object -First 1)

Write-Host "  Packages: $(if($hasPnpmPkgs){'OK'}else{'MISSING'})" -ForegroundColor $(if($hasPnpmPkgs){'Green'}else{'Red'})
Write-Host "  better_sqlite3.node: $(if($hasBetterSqlite3){'OK'}else{'MISSING'})" -ForegroundColor $(if($hasBetterSqlite3){'Green'}else{'Red'})
Write-Host "  electron.exe: $(if($hasElectronBinary){'OK'}else{'MISSING'})" -ForegroundColor $(if($hasElectronBinary){'Green'}else{'Red'})

if (-not $hasPnpmPkgs) {
    Write-Host ""
    Write-Host "  node_modules looks broken. Cleaning and doing full install..." -ForegroundColor Red
    if (Test-Path node_modules) { Remove-Item -Recurse -Force node_modules }

    Write-Host "  Configuring npmmirror..." -ForegroundColor Yellow
    $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    $env:npm_config_electron_mirror = "https://npmmirror.com/mirrors/electron/"
    $env:npm_config_disturl = "https://npmmirror.com/mirrors/electron/"

    pnpm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] pnpm install failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] pnpm install completed" -ForegroundColor Green
} else {
    Write-Host "  Skipping pnpm install (packages already present)" -ForegroundColor Green
}

# --- Step 4: fix Electron binary if missing ---
if (-not $hasElectronBinary) {
    Write-Host "[4/5] Installing Electron binary..." -ForegroundColor Yellow
    $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"

    $electronPkg = Get-ChildItem -Path "node_modules\.pnpm" -Directory | Where-Object { $_.Name -match "^electron@" } | Select-Object -First 1
    if (-not $electronPkg) {
        Write-Host "  [FAIL] electron package not found in .pnpm" -ForegroundColor Red
        exit 1
    }
    $installScript = Join-Path $electronPkg.FullName "node_modules\electron\install.js"
    if (-not (Test-Path $installScript)) {
        Write-Host "  [FAIL] install.js not found at $installScript" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Running: node $installScript" -ForegroundColor DarkGray
    Write-Host ""
    node $installScript
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] Electron download failed (exit code: $LASTEXITCODE)" -ForegroundColor Red
        Write-Host "  If GitHub is blocked, try setting proxy before running this script:"
        Write-Host '    $env:HTTP_PROXY="http://127.0.0.1:7890"; $env:HTTPS_PROXY="http://127.0.0.1:7890"'
        exit 1
    }
    Write-Host "  [OK] Electron binary installed" -ForegroundColor Green
} else {
    Write-Host "[4/5] Electron binary already present, skipping" -ForegroundColor Green
}

# --- Step 5: ensure better-sqlite3 ---
if (-not $hasBetterSqlite3) {
    Write-Host "[5/5] Rebuilding better-sqlite3 native module..." -ForegroundColor Yellow
    pnpm rebuild better-sqlite3
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [FAIL] rebuild failed" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] better_sqlite3.node built" -ForegroundColor Green
} else {
    Write-Host "[5/5] better-sqlite3 already OK, skipping" -ForegroundColor Green
}

# --- Final verification ---
Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  Final verification..." -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan

$finalSqlite = Get-ChildItem -Path "node_modules\.pnpm" -Recurse -Filter "better_sqlite3.node" -ErrorAction SilentlyContinue | Select-Object -First 1
$finalElectron = Get-ChildItem -Path "node_modules\.pnpm" -Recurse -Filter "electron.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

if ($finalSqlite) { Write-Host "  [OK] better_sqlite3.node -> $($finalSqlite.FullName)" -ForegroundColor Green } else { Write-Host "  [FAIL] better_sqlite3.node MISSING" -ForegroundColor Red; exit 1 }
if ($finalElectron) { Write-Host "  [OK] electron.exe        -> $($finalElectron.FullName)" -ForegroundColor Green } else { Write-Host "  [FAIL] electron.exe MISSING" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  [OK] All dependencies ready!" -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Start Electron now? (keeps this window open)" -ForegroundColor Cyan
$choice = Read-Host "Enter Y to start pnpm dev now, or N to skip"
if ($choice -eq "Y" -or $choice -eq "y") {
    Write-Host ""
    Write-Host "Starting pnpm dev ... KEEP THIS WINDOW OPEN!" -ForegroundColor Yellow
    Write-Host ""
    pnpm dev
} else {
    Write-Host "OK. Later click 'Save and Start' in CrawAgent Settings page." -ForegroundColor Green
}
