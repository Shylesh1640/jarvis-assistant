# 🐳 Docker Deployment - Fix Guide

**Issue:** 502 Bad Gateway error when using Docker Compose  
**Status:** ✅ Fixed with automated script + improved frontend

---

## 🚀 **Quick Fix (Automated)**

```powershell
# Run the automated fix script
.\docker_fix.ps1

# Or for a complete rebuild
.\docker_fix.ps1 -FullRebuild

# Or just check status
.\docker_fix.ps1 -CheckOnly
```

This will:
1. ✅ Check Docker is running
2. ✅ Check Ollama on host
3. ✅ Check container status
4. ✅ Review backend logs
5. ✅ Restart containers
6. ✅ Test connectivity

---

## 🔍 **Understanding the 502 Error**

The `502 Bad Gateway` error means:
- ❌ Frontend can connect to the backend container name
- ❌ But the backend container is not responding

**Common causes:**
1. Backend container crashed during startup
2. Database migrations failed
3. Ollama not accessible from backend
4. Backend still initializing (wait 30s)

---

## 📊 **Manual Diagnosis**

### Step 1: Check Container Status
```powershell
docker ps -a | Select-String "jarvis"
```

**Expected:** All 3 containers running (Up status)
```
jarvis-backend      Up
jarvis-frontend     Up  
jarvis-postgres     Up (healthy)
```

### Step 2: Check Backend Logs
```powershell
docker logs jarvis-backend --tail 50
```

**Good signs:**
```
INFO: Initializing database...
✓ Database initialized and migrations applied
✓ Ollama is reachable at http://host.docker.internal:11434
✓ Startup complete
INFO: Application startup complete.
```

**Bad signs:**
```
❌ Ollama is NOT reachable
❌ DB init failed
❌ sqlite3.OperationalError
❌ Connection refused
```

### Step 3: Check Ollama on Host
```powershell
# Ollama must run on HOST, not in container
ollama ps
```

**Should show:** Running models or "No models loaded"

### Step 4: Test Backend Directly
```powershell
curl http://localhost:8000/health
```

**Expected:**
```json
{"status":"ok","ollama_reachable":true}
```

---

## 🛠️ **Manual Fix Steps**

### Fix 1: Backend Container Failed

```powershell
# Check logs for the error
docker logs jarvis-backend --tail 100

# Restart backend
docker compose restart backend

# Wait 30 seconds, then check again
Start-Sleep -Seconds 30
curl http://localhost:8000/health
```

### Fix 2: Database Issues

```powershell
# Stop everything
docker compose down

# Remove old database volume
docker volume rm jarvis-assistant_jarvis_pgdata -f

# Start with fresh database
docker compose up -d --build

# Check logs
docker logs jarvis-backend -f
```

### Fix 3: Ollama Not Accessible

```powershell
# On HOST (not in container), start Ollama
ollama serve

# Test it's accessible
ollama ps
curl http://localhost:11434/api/tags

# Restart backend to reconnect
docker compose restart backend
```

### Fix 4: Models Not Installed

```powershell
# Pull models ON HOST
ollama pull qwen3:8b
ollama pull qwen2.5-coder:7b
ollama pull qwen3:14b

# Verify
ollama list
```

### Fix 5: Complete Rebuild

```powershell
# Stop and remove everything
docker compose down -v

# Remove images
docker images | Select-String "jarvis" | ForEach-Object {
    $id = ($_ -split "\s+")[2]
    docker rmi $id -f
}

# Rebuild from scratch
docker compose up -d --build

# Monitor logs
docker logs jarvis-backend -f
```

---

## 🎯 **Frontend Improvements Made**

I've improved the frontend with:

### 1. **Auto-Detection of Backend URL**
```python
# Tries BACKEND_URL env var first
# Falls back to localhost if Docker backend unreachable
# Shows clear error if neither works
```

### 2. **Automatic Retry Logic**
- Retries failed requests 3 times
- 2-second delay between retries
- Shows retry progress in UI

### 3. **Better Error Messages**
```
Before: "Error contacting backend: Server error '502 Bad Gateway'"

After: Detailed troubleshooting with:
  - What went wrong
  - Why it happened
  - Exact commands to fix it
  - Docker-specific instructions
```

### 4. **Connection Status UI**
- Shows current backend URL
- Refresh button to retest connection
- Expandable troubleshooting panels
- Manual URL override option

### 5. **Smart Error Handling**
- 502 → Backend not running (with fix steps)
- 503 → Ollama not accessible (with fix steps)
- Connection refused → Detailed diagnostics
- Timeout → Background task suggestion

---

## 📋 **Checklist: Is Your Docker Setup Working?**

Run these checks:

```powershell
# 1. Docker running?
docker ps

# 2. Ollama running on host?
ollama ps

# 3. Containers running?
docker ps | Select-String "jarvis"

# 4. Backend healthy?
curl http://localhost:8000/health

# 5. Frontend accessible?
curl http://localhost:8501

# 6. Backend logs good?
docker logs jarvis-backend --tail 20

# 7. Can test a chat?
$body = @{session_id="test"; message="Hello"} | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/chat" -Method Post -Body $body -ContentType "application/json"
```

**All green?** ✅ You're ready!

---

## 🏗️ **Architecture: How Docker Setup Works**

```
┌─────────────────┐
│  Host Machine   │
│                 │
│  ┌───────────┐ │
│  │  Ollama   │ │  ← Runs on HOST (port 11434)
│  └───────────┘ │
│                 │
│  Docker Compose:│
│  ┌───────────┐ │
│  │ Frontend  │ │  → Connects to backend via Docker network
│  │ :8501     │ │
│  └───────────┘ │
│        ↓        │
│  ┌───────────┐ │
│  │ Backend   │ │  → Connects to Ollama via host.docker.internal
│  │ :8000     │ │
│  └───────────┘ │
│        ↓        │
│  ┌───────────┐ │
│  │ Postgres  │ │
│  │ :5432     │ │
│  └───────────┘ │
└─────────────────┘
```

**Key Points:**
1. **Ollama runs on HOST** (not in container)
2. **Backend connects to Ollama via** `host.docker.internal:11434`
3. **Frontend connects to backend via** `http://backend:8000` (Docker network)
4. **Users access via** `http://localhost:8501` (port forwarding)

---

## 🔧 **Common Issues & Solutions**

### Issue: "Cannot connect to Ollama"
**Cause:** Ollama not running on host  
**Fix:**
```powershell
ollama serve  # On host, not in container!
docker compose restart backend
```

### Issue: "Database locked"
**Cause:** Volume conflict  
**Fix:**
```powershell
docker compose down
docker volume rm jarvis-assistant_jarvis_pgdata -f
docker compose up -d
```

### Issue: "Models not found"
**Cause:** Models not pulled on host  
**Fix:**
```powershell
# On HOST
ollama pull qwen3:8b
ollama pull qwen2.5-coder:7b
docker compose restart backend
```

### Issue: "Backend startup takes forever"
**Cause:** Migrations running, models loading  
**Fix:** Wait 1-2 minutes, monitor logs:
```powershell
docker logs jarvis-backend -f
```

### Issue: "Frontend shows 'Backend offline'"
**Cause:** Backend container crashed  
**Fix:**
```powershell
docker logs jarvis-backend --tail 100  # Check error
docker compose restart backend         # Try restart
.\docker_fix.ps1 -FullRebuild         # Or rebuild
```

---

## 🎓 **Best Practices**

### Development
```powershell
# Use local mode (no Docker)
uv run uvicorn jarvis.api.main:app --reload --app-dir src
uv run streamlit run streamlit_app.py
```

### Staging/Production
```powershell
# Use Docker Compose
docker compose up -d --build

# Monitor
docker logs jarvis-backend -f
```

### Before Committing
```powershell
# Test both modes work
.\auto_fix.ps1              # Local
.\docker_fix.ps1            # Docker
```

---

## 📞 **Still Having Issues?**

### Collect Full Diagnostics
```powershell
# Save to file
.\docker_fix.ps1 -CheckOnly > diagnostics.txt
docker logs jarvis-backend --tail 100 >> diagnostics.txt
docker logs jarvis-frontend --tail 50 >> diagnostics.txt
curl http://localhost:8000/health >> diagnostics.txt 2>&1
ollama list >> diagnostics.txt 2>&1
```

### Quick Recovery
```powershell
# Nuclear option - reset everything
docker compose down -v
docker system prune -af
.\docker_fix.ps1 -FullRebuild
```

---

## ✅ **Verification**

Your Docker setup is working when:

1. ✅ `docker ps` shows 3 containers running
2. ✅ `docker logs jarvis-backend` shows "Startup complete"
3. ✅ `curl http://localhost:8000/health` returns ok
4. ✅ `curl http://localhost:8501` returns HTML
5. ✅ Browser at http://localhost:8501 shows green badges
6. ✅ Can send message and get response
7. ✅ Backend logs show no errors

---

**Status:** ✅ Fixed with automated tooling  
**Time to fix:** 5-10 minutes  
**Confidence:** High - Comprehensive error handling added
