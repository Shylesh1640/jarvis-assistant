#!/usr/bin/env python3
"""Setup script to pull required Ollama models.

This script checks which models are needed based on .env configuration
and pulls any missing models.
"""
import subprocess
import sys
from pathlib import Path

# Load .env file
env_file = Path(".env")
if not env_file.exists():
    print("[ERROR] .env file not found. Please create it from .env.example")
    sys.exit(1)

env_vars = {}
with open(env_file, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env_vars[key.strip()] = value.strip().strip('"').strip("'")

# Required models from .env (use set to avoid duplicates)
required_models = set()

general_model = env_vars.get("GENERAL_MODEL", "qwen3:8b")
if general_model:
    required_models.add(general_model)

coding_model = env_vars.get("CODING_MODEL", "qwen2.5-coder:7b")
if coding_model:
    required_models.add(coding_model)

strong_local = env_vars.get("STRONG_LOCAL_MODEL", "qwen3:14b")
use_strong = env_vars.get("USE_STRONG_LOCAL", "true").lower() == "true"
if use_strong and strong_local:
    required_models.add(strong_local)

embedding_model = env_vars.get("EMBEDDING_MODEL", "qwen3-embedding:latest")
if embedding_model:
    required_models.add(embedding_model)

# Convert to sorted list
required_models = sorted(list(required_models))

print("=" * 60)
print("Jarvis Assistant - Model Setup")
print("=" * 60)
print()

# Check Ollama is running
print("[INFO] Checking if Ollama is running...")
try:
    result = subprocess.run(
        ["ollama", "list"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5
    )
    print("[OK] Ollama is running")
    print()
except subprocess.TimeoutExpired:
    print("[ERROR] Ollama is not responding (timeout)")
    print("        Start it with: ollama serve")
    sys.exit(1)
except subprocess.CalledProcessError:
    print("[ERROR] Ollama is not running or not installed")
    print("        Install from: https://ollama.ai")
    print("        Then run: ollama serve")
    sys.exit(1)
except FileNotFoundError:
    print("[ERROR] Ollama is not installed")
    print("        Install from: https://ollama.ai")
    sys.exit(1)

# Get currently installed models
print("[INFO] Checking installed models...")
try:
    result = subprocess.run(
        ["ollama", "list"],
        capture_output=True,
        text=True,
        check=True
    )
    installed_lines = result.stdout.strip().split("\n")[1:]  # Skip header
    installed_models = set()
    for line in installed_lines:
        if line.strip():
            parts = line.split()
            if parts:
                installed_models.add(parts[0])

    print(f"        Found {len(installed_models)} installed model(s)")
    if installed_models:
        for model in sorted(installed_models):
            print(f"        - {model}")
    print()
except Exception as e:
    print(f"[ERROR] Failed to list models: {e}")
    sys.exit(1)

# Check what needs to be pulled
missing_models = []
for model in required_models:
    # Check both with and without tag
    model_base = model.split(":")[0]
    found = False
    for installed in installed_models:
        if installed == model or installed.startswith(model_base + ":"):
            found = True
            break
    if not found:
        missing_models.append(model)

if not missing_models:
    print("[OK] All required models are installed!")
    print()
    print("You're ready to go! Start the backend with:")
    print("   uv run uvicorn jarvis.api.main:app --reload --app-dir src")
    sys.exit(0)

# Pull missing models
print(f"[INFO] Need to pull {len(missing_models)} model(s):")
for model in missing_models:
    print(f"        - {model}")
print()

total = len(missing_models)
for i, model in enumerate(missing_models, 1):
    print(f"[{i}/{total}] Pulling {model}...")
    print("-" * 60)

    try:
        # Run ollama pull with live output (handle encoding issues on Windows)
        process = subprocess.Popen(
            ["ollama", "pull", model],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding='utf-8',
            errors='replace'  # Replace problematic characters
        )

        # Print output in real-time
        for line in process.stdout:
            # Clean line for display
            line = line.replace('\r', '').strip()
            if line:
                print(f"    {line}")

        process.wait()

        if process.returncode == 0:
            print(f"[OK] Successfully pulled {model}")
        else:
            print(f"[ERROR] Failed to pull {model}")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\n[WARNING] Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Error pulling {model}: {e}")
        sys.exit(1)

    print()

print("=" * 60)
print("[OK] Setup complete! All models are installed.")
print("=" * 60)
print()
print("Next steps:")
print("1. Start the backend:")
print("   uv run uvicorn jarvis.api.main:app --reload --app-dir src")
print()
print("2. Start the frontend:")
print("   uv run streamlit run streamlit_app.py")
print()
print("3. Open http://localhost:8501 in your browser")
