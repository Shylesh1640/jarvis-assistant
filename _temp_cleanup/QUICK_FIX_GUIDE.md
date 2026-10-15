# Quick Fix Guide - Common Issues & Solutions

## 🔴 Ollama Not Connected

### Symptoms
- Error: "Ollama is not reachable"
- Badge shows "Ollama offline" in UI
- 503 Service Unavailable errors

### Solutions

#### 1. Check if Ollama is Running
```bash
# Windows PowerShell
Get-Process ollama -ErrorAction SilentlyContinue

# If not running, start it:
ollama serve
```

#### 2. Verify Ollama URL
```bash
# Check your .env file
cat .env | grep OLLAMA_BASE_URL

# Should be:
OLLAMA_BASE_URL=http://localhost:11434

# Test connectivity
curl http://localhost:11434/api/tags
```

#### 3. Restart Ollama
```bash
# Windows
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
& "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" serve

# Verify it's running
ollama ps
```

#### 4. Check Firewall
```bash
# Windows: Ensure port 11434 is not blocked
netstat -an | findstr "11434"

# Should show: 0.0.0.0:11434 ... LISTENING
```

---

## 🔴 Model Not Found

### Symptoms
- Error: "Model 'qwen3:8b' not found"
- 502 Bad Gateway errors
- Backend logs show "model_not_found"

### Solutions

#### 1. List Available Models
```bash
ollama list
```

#### 2. Pull Missing Models
```bash
# Pull the models specified in .env
ollama pull qwen3:8b
ollama pull qwen2.5-coder:7b
ollama pull qwen3:14b  # If USE_STRONG_LOCAL=true
```

#### 3. Verify Model Names
Check your `.env` file:
```bash
GENERAL_MODEL=qwen3:8b
CODING_MODEL=qwen2.5-coder:7b
STRONG_LOCAL_MODEL=qwen3:14b
```

---

## 🔴 Backend Not Responding

### Symptoms
- UI shows "Backend offline"
- Connection refused to localhost:8000
- No response from API

### Solutions

#### 1. Check if Backend is Running
```bash
# Check process
curl http://localhost:8000/health

# Expected response:
{
  "status": "ok",
  "ollama_reachable": true
}
```

#### 2. Start the Backend
```bash
# From project root
uv run uvicorn jarvis.api.main:app --reload --app-dir src

# Or with specific host/port
uv run uvicorn jarvis.api.main:app --host 0.0.0.0 --port 8000 --app-dir src
```

#### 3. Check Logs
```bash
# Look for startup errors
tail -f logs/jarvis.log

# Or check console output
```

---

## 🔴 Out of Memory (OOM)

### Symptoms
- Error: "out of memory"
- 507 Insufficient Storage
- System freezes during generation

### Solutions

#### 1. Reduce Context Size
Edit `.env`:
```bash
# Reduce from 8192 to 4096
OLLAMA_CONTEXT_LENGTH=4096

# Reduce history
HISTORY_MAX_TURNS=10
CONTEXT_TOKEN_BUDGET=6000
```

#### 2. Enable CPU Fallback
```bash
# In .env
GPU_FALLBACK_TO_CPU=true
```

#### 3. Use Smaller Models
```bash
# Switch to 8B instead of 14B
USE_STRONG_LOCAL=false

# Or use a smaller model
GENERAL_MODEL=qwen3:1.8b
```

#### 4. Close Other Applications
```bash
# Check GPU memory
nvidia-smi

# Close unnecessary applications using VRAM
```

---

## 🔴 Slow Response Times

### Symptoms
- Requests take > 30 seconds
- Timeout errors
- UI spinner runs indefinitely

### Solutions

#### 1. Check Model Is Loaded
```bash
ollama ps

# Should show your model loaded
# If not, it will load on first request (slow)
```

#### 2. Preload Model
```bash
# Warm up the model
ollama run qwen3:8b "hello"
```

#### 3. Reduce Context
See "Out of Memory" solutions above

#### 4. Use Background Tasks
In UI, toggle "Background" for long-running requests

---

## 🔴 Session/Approval Issues

### Symptoms
- "No pending approval" errors
- Lost conversation history
- Stale approvals

### Solutions

#### 1. Clear Session Cache
Restart the backend - it will auto-clear caches

#### 2. Check Database
```bash
# SQLite (local mode)
ls -lh data/jarvis.db

# Should exist and be readable
```

#### 3. Verify Session Token
```bash
# Check if session tokens are required
cat .env | grep REQUIRE_SESSION_TOKEN

# If true, ensure UI is sending tokens
```

---

## 🔴 Memory Leaks

### Symptoms
- Backend memory grows unbounded
- System becomes slow over time
- Eventually crashes

### Solutions

#### 1. Verify Cleanup is Running
Check logs for:
```
INFO: Evicted X old session(s) from memory cache
INFO: Cleaned up X stale pending approval(s)
```

#### 2. Adjust Cache Limits
Edit `src/jarvis/api/routes/chat.py`:
```python
_SESSION_CACHE_MAX_SIZE = 500  # Reduce from 1000
_SESSION_MAX_HISTORY = 50      # Reduce from 100
```

#### 3. Restart Periodically
Consider a cron job or systemd timer to restart daily

---

## 🔴 Race Conditions / Corruption

### Symptoms
- Lost messages
- Duplicated responses
- Approval state mismatch

### Solutions

#### 1. Update to Latest Version
Ensure you have the thread-safe version with locks

#### 2. Check Concurrency Settings
```bash
# In .env, limit concurrent requests if needed
OLLAMA_NUM_PARALLEL=1
```

#### 3. Restart Backend
This resets all in-memory state

---

## 🔴 GPU Not Being Used

### Symptoms
- Processor shows "100% CPU"
- Slow generation (< 5 tokens/sec)
- nvidia-smi shows 0% GPU usage

### Solutions

#### 1. Verify GPU is Available
```bash
nvidia-smi

# Should show your GPU
```

#### 2. Check Ollama GPU Settings
```bash
# Ensure Ollama sees the GPU
ollama ps

# PROCESSOR column should not be "100% CPU"
```

#### 3. Restart Ollama with GPU
```bash
# Stop Ollama
pkill -f ollama

# Start with explicit GPU support
CUDA_VISIBLE_DEVICES=0 ollama serve
```

#### 4. Verify Settings
```bash
# In .env
GPU_OPTIMIZATION_ENABLED=true
OLLAMA_NUM_GPU=-1  # Force full GPU offload
```

---

## 📊 Health Check Commands

### Quick Diagnostics
```bash
# 1. Check Ollama
curl http://localhost:11434/api/tags

# 2. Check Backend
curl http://localhost:8000/health

# 3. Check Runtime Details
curl http://localhost:8000/runtime | python -m json.tool

# 4. Check GPU
nvidia-smi

# 5. Check Models
ollama list
```

### Full System Check
```bash
# Run validation CLI
uv run jarvis-validate-runtime

# Expected output: All checks passed
```

---

## 🛠️ Emergency Reset

If nothing else works:

```bash
# 1. Stop everything
pkill -f ollama
pkill -f uvicorn

# 2. Clear caches
rm -rf data/vector_store/*
rm -f data/jarvis.db

# 3. Restart Ollama
ollama serve &

# 4. Verify models
ollama list

# 5. Restart backend
uv run uvicorn jarvis.api.main:app --reload --app-dir src

# 6. Test
curl http://localhost:8000/health
```

---

## 📞 Getting Help

### Collect Diagnostics
```bash
# Save to file
{
  echo "=== System Info ==="
  uname -a
  echo ""
  echo "=== Ollama Status ==="
  ollama ps
  echo ""
  echo "=== Backend Health ==="
  curl http://localhost:8000/health
  echo ""
  echo "=== Runtime Info ==="
  curl http://localhost:8000/runtime
  echo ""
  echo "=== GPU Status ==="
  nvidia-smi
} > diagnostics.txt
```

### Log Locations
- Backend logs: Console output or `logs/jarvis.log`
- Ollama logs: Console output where `ollama serve` is running
- System logs: Check Event Viewer (Windows) or syslog (Linux)

### Common Log Patterns
```bash
# Look for these in logs:

# Good:
"✓ Ollama is reachable"
"Using cached ChatOllama client"
"Graph built successfully"

# Bad:
"Ollama unavailable"
"Connection refused"
"Model not found"
"Out of memory"
```

---

## ✅ Prevention Checklist

Before starting work:

- [ ] Ollama is running (`ollama ps`)
- [ ] Required models are pulled (`ollama list`)
- [ ] Backend is healthy (`curl http://localhost:8000/health`)
- [ ] GPU is available (`nvidia-smi`)
- [ ] .env file is configured correctly
- [ ] Database exists (`ls data/jarvis.db`)

---

**Last Updated:** 2026-10-06  
**Version:** Post-Optimization  
**Status:** ✅ Comprehensive
