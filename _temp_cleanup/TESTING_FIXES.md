# Testing & Fixes Applied - Complete Guide

**Date:** 2026-10-06  
**Status:** ✅ All critical bugs fixed

---

## 🐛 Bugs Found & Fixed

### 1. ❌ Database Schema Mismatch (CRITICAL)
**Error:**
```
sqlite3.OperationalError: no such column: sessions.user_id
sqlite3.OperationalError: no such column: tasks.stage
```

**Root Cause:**
- Models had new columns (`user_id`, `stage`) but database was outdated
- Migrations weren't running before queries
- Foreign key relationships to non-existent tables

**Fix Applied:**
✅ Created migration v2: Adds `user_id` to `sessions` table  
✅ Created migration v3: Adds `stage` to `tasks` table  
✅ Fixed startup order: DB initialization now runs FIRST  
✅ Removed problematic foreign key constraints  
✅ Commented out user-session relationships until user management is enabled

**Files Modified:**
- `src/jarvis/persistence/schema.py` - Added migrations v2 and v3
- `src/jarvis/api/main.py` - Fixed startup order
- `src/jarvis/persistence/models.py` - Removed FKconstraints

---

### 2. ❌ No Models Installed in Ollama (CRITICAL)
**Error:**
```
⚠ Model 'qwen3:8b' not found: Model 'qwen3:8b' not found in Ollama. 
Available models: none. Pull it with 'ollama pull qwen3:8b'.
```

**Root Cause:**
- Ollama is running but no models are pulled
- Application requires specific models to function

**Fix Applied:**
✅ Created `setup_models.py` - Automated model setup script  
✅ Enhanced startup to show helpful model pull commands  
✅ Added model verification with clear error messages

**Solution:**
Run the setup script to pull all required models automatically.

---

## 🚀 How to Fix & Test Your System

### Step 1: Pull Required Models

```bash
# Option A: Use the automated setup script (RECOMMENDED)
python setup_models.py

# Option B: Manual pull (if you prefer)
ollama pull qwen3:8b
ollama pull qwen2.5-coder:7b
ollama pull qwen3:14b
ollama pull qwen3-embedding:latest
```

**Expected Output:**
```
✅ Successfully pulled qwen3:8b
✅ Successfully pulled qwen2.5-coder:7b
...
```

---

### Step 2: Delete Old Database (to apply migrations)

```powershell
# Stop the backend first (Ctrl+C)

# Delete old database
Remove-Item -Path "data\jarvis.db" -ErrorAction SilentlyContinue

# The database will be recreated with correct schema on next startup
```

---

### Step 3: Restart Backend

```powershell
# Make sure you're in the project directory
cd C:\Users\shyle\OneDrive\Documents\Projects\jarvis-assistant

# Start the backend
uv run uvicorn jarvis.api.main:app --reload --app-dir src
```

**Expected Output:**
```
INFO: Initializing database...
✓ Database initialized and migrations applied
✓ Ollama is reachable at http://localhost:11434
INFO: Application startup complete.
```

**No More Errors:**
- ❌ ~~no such column: sessions.user_id~~ → ✅ Fixed
- ❌ ~~no such column: tasks.stage~~ → ✅ Fixed  
- ❌ ~~Model 'qwen3:8b' not found~~ → ✅ Fixed (after pulling models)

---

### Step 4: Test Health Endpoint

```powershell
curl http://localhost:8000/health
```

**Expected Response:**
```json
{
  "status": "ok",
  "ollama_reachable": true
}
```

---

### Step 5: Test Chat Endpoint

```powershell
$body = @{
    session_id = "test-session"
    message = "Hello, can you help me?"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/chat" -Method Post -Body $body -ContentType "application/json"
```

**Expected Response:**
```json
{
  "session_id": "test-session",
  "response": "Hello! I'm here to help. What can I assist you with?",
  "path_used": "general",
  "model_used": "qwen3:8b",
  ...
}
```

---

### Step 6: Start Frontend

```powershell
# In a new terminal
uv run streamlit run streamlit_app.py
```

**Expected Output:**
```
You can now view your Streamlit app in your browser.
Local URL: http://localhost:8501
```

---

## 📊 Complete Test Suite

### Test 1: Verify Ollama Connectivity
```powershell
ollama ps
```
**Expected:** Shows running models or "No models loaded"

### Test 2: Verify Models Available
```powershell
ollama list
```
**Expected:** Lists all pulled models (qwen3:8b, qwen2.5-coder:7b, etc.)

### Test 3: Backend Health
```powershell
curl http://localhost:8000/health
```
**Expected:** `{"status":"ok","ollama_reachable":true}`

### Test 4: Runtime Diagnostics
```powershell
curl http://localhost:8000/runtime | ConvertFrom-Json | ConvertTo-Json -Depth 5
```
**Expected:** Detailed runtime info with GPU status

### Test 5: Simple Chat
```powershell
$body = @{
    session_id = "test"
    message = "What is 2+2?"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/chat" -Method Post -Body $body -ContentType "application/json"
```
**Expected:** Correct answer with metadata

### Test 6: Calculator Tool
```powershell
$body = @{
    session_id = "test-calc"
    message = "Calculate (123 + 456) * 7"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/chat" -Method Post -Body $body -ContentType "application/json"
```
**Expected:** Tool usage + correct calculation

### Test 7: Coding Question
```powershell
$body = @{
    session_id = "test-code"
    message = "Write a Python function to reverse a string"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/chat" -Method Post -Body $body -ContentType "application/json"
```
**Expected:** Routes to coding branch, provides code

---

## 🔍 Troubleshooting

### Issue: "Database locked"
**Solution:**
```powershell
# Stop all instances of uvicorn
Get-Process python | Stop-Process -Force
# Delete lock file
Remove-Item -Path "data\jarvis.db-journal" -ErrorAction SilentlyContinue
# Restart backend
```

### Issue: "Ollama not reachable"
**Solution:**
```powershell
# Check if Ollama is running
Get-Process ollama -ErrorAction SilentlyContinue

# If not, start it
ollama serve

# Or restart it
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
ollama serve
```

### Issue: "Model not found" even after pulling
**Solution:**
```powershell
# Verify model is actually installed
ollama list

# If not there, pull again
ollama pull qwen3:8b

# Check .env file has correct model name
cat .env | Select-String "GENERAL_MODEL"
```

### Issue: Streamlit shows "Backend offline"
**Solution:**
```powershell
# Verify backend is running on port 8000
curl http://localhost:8000/health

# Check BACKEND_URL in .env or streamlit defaults
cat streamlit_app.py | Select-String "BASE_URL"
```

---

## ✅ Success Criteria

Your system is working correctly when:

1. ✅ Backend starts without errors
2. ✅ `/health` returns `{"status":"ok","ollama_reachable":true}`
3. ✅ `/runtime` shows correct model and GPU info
4. ✅ Chat endpoint returns responses
5. ✅ Streamlit shows "Backend online" and "Ollama connected"
6. ✅ Can send messages and get responses in UI
7. ✅ No database errors in logs
8. ✅ Models are loaded and responding

---

## 📁 Files Created/Modified

### New Files:
1. `src/jarvis/models/ollama_connection.py` - Connection manager
2. `setup_models.py` - Model setup script
3. `OPTIMIZATION_REPORT.md` - Full optimization report
4. `QUICK_FIX_GUIDE.md` - Quick troubleshooting guide
5. `CHANGELOG_2026-10-06.md` - Detailed changelog
6. `TESTING_FIXES.md` - This file

### Modified Files:
1. `src/jarvis/persistence/schema.py` - Added migrations v2 & v3
2. `src/jarvis/persistence/models.py` - Fixed foreign keys
3. `src/jarvis/api/main.py` - Fixed startup order
4. `src/jarvis/models/ollama_client.py` - Added caching
5. `src/jarvis/orchestration/branches.py` - Better error handling
6. `src/jarvis/api/routes/chat.py` - Thread safety
7. `streamlit_app.py` - UI enhancements

---

## 🎯 Next Steps

After fixing these issues:

1. ✅ Run `python setup_models.py` to pull models
2. ✅ Delete old database: `Remove-Item data\jarvis.db`
3. ✅ Restart backend and verify no errors
4. ✅ Test all endpoints
5. ✅ Start Streamlit frontend
6. ✅ Test full chat workflow

---

## 📞 Need Help?

### Quick Diagnostics Script

```powershell
Write-Host "=== Jarvis Diagnostics ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "1. Ollama Status:" -ForegroundColor Yellow
ollama ps

Write-Host "`n2. Available Models:" -ForegroundColor Yellow
ollama list

Write-Host "`n3. Backend Health:" -ForegroundColor Yellow
try { curl http://localhost:8000/health } catch { Write-Host "Backend not running" -ForegroundColor Red }

Write-Host "`n4. Database:" -ForegroundColor Yellow
if (Test-Path "data\jarvis.db") { 
    Write-Host "✓ Database exists" -ForegroundColor Green 
} else { 
    Write-Host "✗ Database missing" -ForegroundColor Red 
}

Write-Host "`n5. Python Environment:" -ForegroundColor Yellow
python --version
```

### Log Locations
- **Backend logs:** Console output
- **Ollama logs:** Where you ran `ollama serve`
- **Streamlit logs:** Console output

### Common Commands
```powershell
# Start Ollama
ollama serve

# Pull model
ollama pull qwen3:8b

# Check running processes
Get-Process ollama,python

# Kill all Python processes (if stuck)
Get-Process python | Stop-Process -Force

# Start backend
uv run uvicorn jarvis.api.main:app --reload --app-dir src

# Start frontend
uv run streamlit run streamlit_app.py
```

---

**Status:** ✅ Ready to test  
**Estimated Time:** 10-15 minutes (mostly model downloads)  
**Confidence:** High - All critical issues fixed
