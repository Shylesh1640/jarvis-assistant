# Jarvis Assistant - Automated Fix Script
# This script automatically fixes all issues and sets up your system

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Jarvis Assistant - Automated Fix & Setup" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Ollama is running
Write-Host "[1/5] Checking Ollama..." -ForegroundColor Yellow
try {
    ollama ps | Out-Null
    Write-Host "      [OK] Ollama is running" -ForegroundColor Green
} catch {
    Write-Host "      [ERROR] Ollama is not running!" -ForegroundColor Red
    Write-Host "      Please start Ollama first: ollama serve" -ForegroundColor Red
    exit 1
}

# Step 2: Pull required models
Write-Host ""
Write-Host "[2/5] Pulling required models..." -ForegroundColor Yellow
Write-Host "      This may take 10-15 minutes (one-time setup)" -ForegroundColor Gray

python setup_models.py

if ($LASTEXITCODE -ne 0) {
    Write-Host "      [ERROR] Failed to pull models" -ForegroundColor Red
    exit 1
}

# Step 3: Stop any running backend
Write-Host ""
Write-Host "[3/5] Stopping existing backend..." -ForegroundColor Yellow
Get-Process python -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*uvicorn*"} | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Write-Host "      [OK] Backend stopped" -ForegroundColor Green

# Step 4: Backup and delete old database
Write-Host ""
Write-Host "[4/5] Fixing database schema..." -ForegroundColor Yellow

if (Test-Path "data\jarvis.db") {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backup = "data\jarvis.db.backup.$timestamp"
    Copy-Item "data\jarvis.db" $backup
    Write-Host "      [OK] Database backed up to: $backup" -ForegroundColor Green

    Remove-Item "data\jarvis.db" -Force
    Write-Host "      [OK] Old database deleted (will be recreated with correct schema)" -ForegroundColor Green
} else {
    Write-Host "      [INFO] No old database found" -ForegroundColor Gray
}

# Step 5: Test backend startup
Write-Host ""
Write-Host "[5/5] Testing backend startup..." -ForegroundColor Yellow
Write-Host "      Starting backend (this will stay running)..." -ForegroundColor Gray
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Starting backend - watch for success messages:" -ForegroundColor Cyan
Write-Host "  ✓ Database initialized and migrations applied" -ForegroundColor Gray
Write-Host "  ✓ Ollama is reachable" -ForegroundColor Gray
Write-Host "  ✓ Startup complete" -ForegroundColor Gray
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Start backend in foreground so user can see output
uv run uvicorn jarvis.api.main:app --reload --app-dir src
