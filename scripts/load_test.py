import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

API_URL = "http://localhost:8000"
TIMEOUT = 120.0

QUERIES = [
    # Billing (5)
    ("billing", "What ICD-10 codes have the highest claim rejection rate?"),
    ("billing", "Show me all billing claims with upcoding errors."),
    ("billing", "Which payer rejects the most claims and why?"),
    ("billing", "List all claims under review with their rejection reasons."),
    ("billing", "What is our total pending claim value this month?"),
    # Compliance (5)
    ("compliance", "List all Critical HIPAA violations from the last 30 days."),
    ("compliance", "How many JCI violations are currently open?"),
    ("compliance", "Which department has the most protocol deviations?"),
    ("compliance", "Show me all findings where patient consent was missing."),
    ("compliance", "What is our overall compliance audit pass rate?"),
    # Pharmacy (5)
    ("pharmacy", "Which drugs are expiring within 30 days?"),
    ("pharmacy", "Show all drugs below reorder threshold with stockout risk."),
    ("pharmacy", "What is our total inventory value at expiry risk?"),
    ("pharmacy", "Which drug categories have the most expired batches?"),
    ("pharmacy", "List all critical reorder alerts with supplier lead times."),
]


async def fire_query(client: httpx.AsyncClient, idx: int, domain: str, query: str) -> dict:
    start = time.perf_counter()
    try:
        response = await client.post(
            f"{API_URL}/v1/chat/completions",
            json={"model": "healthcare-bot", "messages": [{"role": "user", "content": query}]},
            timeout=TIMEOUT,
        )
        elapsed = time.perf_counter() - start
        ok = response.status_code == 200
        content_len = len(response.json()["choices"][0]["message"]["content"]) if ok else 0
        return {
            "idx": idx + 1, "domain": domain, "status": "OK" if ok else "FAIL",
            "http": response.status_code, "elapsed": elapsed, "response_len": content_len,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return {"idx": idx + 1, "domain": domain, "status": "ERROR",
                "http": 0, "elapsed": elapsed, "error": str(exc)[:60]}


async def main():
    print(f"\n{'═'*60}")
    print("  Healthcare Bot — Load Test (15 concurrent queries)")
    print(f"{'═'*60}\n")
    print(f"  Target: {API_URL}")
    print(f"  Queries: {len(QUERIES)} across billing / compliance / pharmacy\n")

    start_all = time.perf_counter()
    async with httpx.AsyncClient() as client:
        tasks = [fire_query(client, i, d, q) for i, (d, q) in enumerate(QUERIES)]
        results = await asyncio.gather(*tasks)
    total_elapsed = time.perf_counter() - start_all

    print(f"  {'#':<4} {'Domain':<12} {'Status':<8} {'HTTP':<6} {'Time (s)':<10} {'Resp Len'}")
    print(f"  {'─'*4} {'─'*12} {'─'*8} {'─'*6} {'─'*10} {'─'*10}")

    ok_count    = 0
    times       = []
    for r in sorted(results, key=lambda x: x["idx"]):
        status_color = "✓" if r["status"] == "OK" else "✗"
        print(f"  {status_color} {r['idx']:<3} {r['domain']:<12} {r['status']:<8} "
              f"{r['http']:<6} {r['elapsed']:.2f}s{'':>4} {r.get('response_len', 0)}")
        if r["status"] == "OK":
            ok_count += 1
            times.append(r["elapsed"])

    print(f"\n{'─'*60}")
    print(f"  Results       : {ok_count}/{len(QUERIES)} passed")
    print(f"  Total time    : {total_elapsed:.2f}s (all concurrent)")
    if times:
        times_sorted = sorted(times)
        p95_idx = int(len(times_sorted) * 0.95)
        print(f"  Avg resp time : {sum(times)/len(times):.2f}s")
        print(f"  P95 resp time : {times_sorted[min(p95_idx, len(times_sorted)-1)]:.2f}s")
        print(f"  Max resp time : {max(times):.2f}s")
        p95_pass = times_sorted[min(p95_idx, len(times_sorted)-1)] < 8.0
        print(f"\n  Phase 8 Check : P95 < 8s → {'✓ PASS' if p95_pass else '✗ FAIL'}")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    asyncio.run(main())