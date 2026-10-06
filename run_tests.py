#!/usr/bin/env python3
"""Test script to validate Jarvis Assistant with easy and complex prompts."""
import json
import time
from datetime import datetime
import httpx

BASE_URL = "http://localhost:8000"
SESSION_ID = f"test-session-{int(time.time())}"

def test_prompt(prompt_data: dict, prompt_type: str) -> dict:
    """Test a single prompt and return results."""
    print(f"\n{'='*70}")
    print(f"[{prompt_type.upper()}] {prompt_data['name']}")
    print(f"{'='*70}")
    print(f"Prompt: {prompt_data['message']}")
    print(f"Expected: {prompt_data['expected']}")
    print("-" * 70)

    start_time = time.time()

    try:
        response = httpx.post(
            f"{BASE_URL}/chat",
            json={
                "session_id": SESSION_ID,
                "message": prompt_data["message"],
                "history": []
            },
            timeout=60.0
        )
        response.raise_for_status()
        data = response.json()

        elapsed = time.time() - start_time

        result = {
            "name": prompt_data["name"],
            "type": prompt_type,
            "success": True,
            "response": data.get("response", ""),
            "path_used": data.get("path_used"),
            "model_used": data.get("model_used"),
            "tools_used": data.get("tools_used", []),
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": datetime.now().isoformat()
        }

        # Display results
        print(f"✓ SUCCESS ({elapsed:.2f}s)")
        print(f"Path: {result['path_used']} | Model: {result['model_used']}")
        if result['tools_used']:
            print(f"Tools: {', '.join(result['tools_used'])}")
        print(f"\nResponse:\n{result['response'][:500]}...")

        return result

    except httpx.HTTPError as exc:
        elapsed = time.time() - start_time
        print(f"✗ FAILED ({elapsed:.2f}s)")
        print(f"Error: {exc}")

        return {
            "name": prompt_data["name"],
            "type": prompt_type,
            "success": False,
            "error": str(exc),
            "elapsed_seconds": round(elapsed, 2),
            "timestamp": datetime.now().isoformat()
        }

def main():
    """Run all test prompts."""
    print("="*70)
    print("JARVIS ASSISTANT - COMPREHENSIVE TEST SUITE")
    print("="*70)
    print(f"Backend: {BASE_URL}")
    print(f"Session: {SESSION_ID}")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Check backend health
    print("\n[INFO] Checking backend health...")
    try:
        health = httpx.get(f"{BASE_URL}/health", timeout=5.0)
        health.raise_for_status()
        health_data = health.json()
        print(f"✓ Backend: {health_data.get('status')}")
        print(f"✓ Ollama: {'connected' if health_data.get('ollama_reachable') else 'offline'}")
    except Exception as exc:
        print(f"✗ Backend not reachable: {exc}")
        return

    # Load test prompts
    with open("test_prompts.json", encoding="utf-8") as f:
        prompts = json.load(f)

    results = {
        "easy": [],
        "complex": []
    }

    # Test easy prompts
    print("\n" + "="*70)
    print("PHASE 1: EASY PROMPTS")
    print("="*70)

    for prompt in prompts["easy_prompts"]:
        result = test_prompt(prompt, "easy")
        results["easy"].append(result)
        time.sleep(1)  # Brief pause between tests

    # Test complex prompts
    print("\n" + "="*70)
    print("PHASE 2: COMPLEX PROMPTS")
    print("="*70)

    for prompt in prompts["complex_prompts"]:
        result = test_prompt(prompt, "complex")
        results["complex"].append(result)
        time.sleep(2)  # Longer pause for complex prompts

    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)

    easy_success = sum(1 for r in results["easy"] if r.get("success"))
    complex_success = sum(1 for r in results["complex"] if r.get("success"))

    total_tests = len(results["easy"]) + len(results["complex"])
    total_success = easy_success + complex_success

    print(f"\nEasy Prompts: {easy_success}/{len(results['easy'])} passed")
    print(f"Complex Prompts: {complex_success}/{len(results['complex'])} passed")
    print(f"\nOverall: {total_success}/{total_tests} passed ({total_success/total_tests*100:.1f}%)")

    # Calculate average times
    easy_times = [r['elapsed_seconds'] for r in results['easy'] if r.get('success')]
    complex_times = [r['elapsed_seconds'] for r in results['complex'] if r.get('success')]

    if easy_times:
        print(f"\nAverage time (easy): {sum(easy_times)/len(easy_times):.2f}s")
    if complex_times:
        print(f"Average time (complex): {sum(complex_times)/len(complex_times):.2f}s")

    # Save detailed results
    output_file = f"test_results_{int(time.time())}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({
            "summary": {
                "total_tests": total_tests,
                "passed": total_success,
                "failed": total_tests - total_success,
                "success_rate": f"{total_success/total_tests*100:.1f}%",
                "timestamp": datetime.now().isoformat()
            },
            "results": results
        }, f, indent=2, ensure_ascii=False)

    print(f"\nDetailed results saved to: {output_file}")
    print("\n" + "="*70)

    if total_success == total_tests:
        print("✓ ALL TESTS PASSED!")
    else:
        print(f"⚠ {total_tests - total_success} test(s) failed")

    print("="*70)

if __name__ == "__main__":
    main()
