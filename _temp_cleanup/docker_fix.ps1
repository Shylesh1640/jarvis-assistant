# Jarvis Assistant - Docker Fix & Diagnostic Script
# This script diagnoses and fixes Docker deployment issues

param(
    [switch]$FullRebuild,
    [switch]$CheckOnly
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Jarvis Assistant - Docker Diagnostics & Fix" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Check Docker is running
Write-Host "[1/8] Checking Docker..." -ForegroundColor Yellow
try {
    docker ps | Out-Null
    Write-Host "      [OK] Docker is running" -ForegroundColor Green
} catch {
    Write-Host "      [ERROR] Docker is not running!" -ForegroundColor Red
    Write-Host "      Start Docker Desktop and try again" -ForegroundColor Red
    exit 1
}

# Step 2: Check Ollama on host
Write-Host ""
Write-Host "[2/8] Checking Ollama on host..." -ForegroundColor Yellow
try {
    ollama ps | Out-Null
    Write-Host "      [OK] Ollama is running on host" -ForegroundColor Green
} catch {
    Write-Host "      [WARNING] Ollama is not running on host!" -ForegroundColor Yellow
    Write-Host "      Backend won't be able to use local models" -ForegroundColor Yellow
    Write-Host "      Start with: ollama serve" -ForegroundColor Yellow
}

# Step 3: Check container status
Write-Host ""
Write-Host "[3/8] Checking containers..." -ForegroundColor Yellow
$containers = docker ps -a --filter "name=jarvis" --format "{{.Names}}\t{{.Status}}"
if ($containers) {
    Write-Host "      Found containers:" -ForegroundColor Gray
    $containers | ForEach-Object {
        $parts = $_ -split "`t"
        $name = $parts[0]
        $status = $parts[1]
        if ($status -like "*Up*") {
            Write-Host "      ✓ $name : $status" -ForegroundColor Green
        } else {
            Write-Host "      ✗ $name : $status" -ForegroundColor Red
        }
    }
} else {
    Write-Host "      [INFO] No Jarvis containers found" -ForegroundColor Gray
}

if ($CheckOnly) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "Check complete. Run without -CheckOnly to fix issues." -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    exit 0
}

# Step 4: Check backend logs for errors
Write-Host ""
Write-Host "[4/8] Checking backend logs..." -ForegroundColor Yellow
$backendExists = docker ps -a --filter "name=jarvis-backend" --format "{{.Names}}"
if ($backendExists) {
    Write-Host "      Last 10 log lines:" -ForegroundColor Gray
    docker logs jarvis-backend --tail 10 2>&1 | ForEach-Object {
        if ($_ -match "ERROR|error|Error|FAILED|failed") {
            Write-Host "      $_" -ForegroundColor Red
        } elseif ($_ -match "WARNING|warning|Warning") {
            Write-Host "      $_" -ForegroundColor Yellow
        } else {
            Write-Host "      $_" -ForegroundColor Gray
        }
    }
} else {
    Write-Host "      [INFO] Backend container not found" -ForegroundColor Gray
}

# Step 5: Stop containers
Write-Host ""
Write-Host "[5/8] Stopping containers..." -ForegroundColor Yellow
docker compose down
Write-Host "      [OK] Containers stopped" -ForegroundColor Green

# Step 6: Delete old database (if full rebuild)
if ($FullRebuild) {
    Write-Host ""
    Write-Host "[6/8] Removing old data (full rebuild)..." -ForegroundColor Yellow
    docker volume rm jarvis-assistant_jarvis_pgdata -f 2>$null
    docker volume rm jarvis-assistant_jarvis_data -f 2>$null
    Write-Host "      [OK] Old data removed" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "[6/8] Keeping existing data..." -ForegroundColor Yellow
    Write-Host "      Use -FullRebuild to reset database" -ForegroundColor Gray
}

# Step 7: Pull models (if needed)
Write-Host ""
Write-Host "[7/8] Checking required models..." -ForegroundColor Yellow
$models = ollama list 2>&1
if ($models -match "qwen3:8b" -and $models -match "qwen2.5-coder") {
    Write-Host "      [OK] Required models are installed" -ForegroundColor Green
} else {
    Write-Host "      [WARNING] Some models are missing" -ForegroundColor Yellow
    Write-Host "      Run: python setup_models.py" -ForegroundColor Yellow
}

# Step 8: Start containers
Write-Host ""
Write-Host "[8/8] Starting containers..." -ForegroundColor Yellow
if ($FullRebuild) {
    Write-Host "      Building and starting (this may take a few minutes)..." -ForegroundColor Gray
    docker compose up -d --build
} else {
    Write-Host "      Starting existing images..." -ForegroundColor Gray
    docker compose up -d
}

if ($LASTEXITCODE -eq 0) {
    Write-Host "      [OK] Containers started" -ForegroundColor Green
} else {
    Write-Host "      [ERROR] Failed to start containers" -ForegroundColor Red
    exit 1
}

# Wait for services to be ready
Write-Host ""
Write-Host "Waiting for services to start..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

# Check health
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Testing Services" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Test backend
Write-Host ""
Write-Host "Backend health:" -ForegroundColor Yellow
for ($i = 1; $i -le 10; $i++) {
    try {
        $response = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2
        if ($response.status -eq "ok") {
            Write-Host "  ✓ Backend is healthy" -ForegroundColor Green
            if ($response.ollama_reachable) {
                Write-Host "  ✓ Backend can reach Ollama" -ForegroundColor Green
            } else {
                Write-Host "  ✗ Backend cannot reach Ollama" -ForegroundColor Red
                Write-Host "    Start Ollama on host: ollama serve" -ForegroundColor Yellow
            }
            break
        }
    } catch {
        if ($i -eq 10) {
            Write-Host "  ✗ Backend is not responding" -ForegroundColor Red
            Write-Host "    Check logs: docker logs jarvis-backend" -ForegroundColor Yellow
        } else {
            Write-Host "  Waiting for backend... ($i/10)" -ForegroundColor Gray
            Start-Sleep -Seconds 2
        }
    }
}

# Test frontend
Write-Host ""
Write-Host "Frontend:" -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8501" -TimeoutSec 2 -UseBasicParsing
    Write-Host "  ✓ Frontend is accessible" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Frontend is not responding" -ForegroundColor Red
    Write-Host "    Check logs: docker logs jarvis-frontend" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "1. Open http://localhost:8501 in your browser" -ForegroundColor White
Write-Host "2. Check backend logs: docker logs jarvis-backend -f" -ForegroundColor White
Write-Host "3. Check frontend logs: docker logs jarvis-frontend -f" -ForegroundColor White
Write-Host ""
Write-Host "Troubleshooting:" -ForegroundColor Cyan
Write-Host "  - Backend errors: docker logs jarvis-backend --tail 50" -ForegroundColor White
Write-Host "  - Restart: docker compose restart" -ForegroundColor White
Write-Host "  - Full rebuild: .\docker_fix.ps1 -FullRebuild" -ForegroundColor White
Write-Host "  - Diagnostics only: .\docker_fix.ps1 -CheckOnly" -ForegroundColor White
