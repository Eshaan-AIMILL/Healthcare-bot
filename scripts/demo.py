import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

API_URL = "http://localhost:8000"

DEMOS = [
    {
        "step": 1,
        "persona": "CFO / Revenue Cycle Manager",
        "domain": "BILLING AGENT",
        "color": "\033[91m",  # red
        "query": (
            "What is our overall claim rejection rate this month, "
            "which ICD-10 codes are driving the most rejections, "
            "and what correction actions should the billing team take immediately?"
        ),
    },
    {
        "step": 2,
        "persona": "Chief Compliance Officer",
        "domain": "COMPLIANCE AGENT",
        "color": "\033[93m",  # yellow
        "query": (
            "Show me all Critical HIPAA violations logged in the last 30 days, "
            "classify each by regulation section, and recommend resolution actions."
        ),
    },
    {
        "step": 3,
        "persona": "Head of Pharmacy / Supply Chain",
        "domain": "PHARMACY AGENT",
        "color": "\033[92m",  # green
        "query": (
            "Which drugs are expiring within 60 days, what is our total cost exposure, "
            "and which items need an emergency reorder right now?"
        ),
    },
    {
        "step": 4,
        "persona": "Patient Experience Director",
        "domain": "PATIENT SUPPORT AGENT",
        "color": "\033[96m",  # cyan
        "query": (
            "How many appointment complaints breached SLA this week, "
            "what is the average delay in minutes, "
            "and which patients are eligible for compensation?"
        ),
    },
    {
        "step": 5,
        "persona": "Logistics & Operations Manager",
        "domain": "DISPATCH AGENT",
        "color": "\033[95m",  # purple
        "query": (
            "What is our on-time delivery rate for emergency medical dispatches, "
            "which routes have the highest SLA breach risk, "
            "and what is the recommended route optimisation for our next emergency delivery?"
        ),
    },
]

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"


async def run_demo():
    print(f"\n{BOLD}{'═'*65}{RESET}")
    print(f"{BOLD}  Healthcare Bot — Live Demo Walkthrough{RESET}")
    print(f"{BOLD}{'═'*65}{RESET}")
    print(f"{DIM}  5 personas · 5 agents · fully automated{RESET}\n")

    async with httpx.AsyncClient(timeout=120.0) as client:

        # Health check first
        try:
            r = await client.get(f"{API_URL}/health")
            h = r.json()
            ollama_status = "✓ Connected" if h.get("ollama_connected") else "✗ Offline"
            print(f"  API Status   : ✓ Online")
            print(f"  Ollama       : {ollama_status}")
            print(f"  Model        : {h.get('model', '—')}\n")
        except Exception:
            print("  ✗ Cannot reach API at", API_URL)
            print("  Make sure: uvicorn app.main:app --port 8000 --reload\n")
            return

        for demo in DEMOS:
            c = demo["color"]
            print(f"{c}{'─'*65}{RESET}")
            print(f"{c}{BOLD}  STEP {demo['step']} — {demo['domain']}{RESET}")
            print(f"{DIM}  Persona : {demo['persona']}{RESET}")
            print(f"{DIM}  Query   : {demo['query'][:80]}...{RESET}\n")

            start = time.perf_counter()
            try:
                response = await client.post(
                    f"{API_URL}/v1/chat/completions",
                    json={
                        "model": "healthcare-bot",
                        "messages": [{"role": "user", "content": demo["query"]}],
                    },
                )
                elapsed = time.perf_counter() - start

                if response.status_code == 200:
                    content = response.json()["choices"][0]["message"]["content"]
                    print(f"{BOLD}  Response ({elapsed:.1f}s):{RESET}")
                    # Print with indentation
                    for line in content.split("\n"):
                        print(f"  {line}")
                    print()
                else:
                    print(f"  ✗ HTTP {response.status_code}\n")

            except Exception as exc:
                elapsed = time.perf_counter() - start
                print(f"  ✗ Error after {elapsed:.1f}s: {exc}\n")

            # Brief pause between demos for readability
            if demo["step"] < 5:
                await asyncio.sleep(1)

    print(f"{BOLD}{'═'*65}{RESET}")
    print(f"{BOLD}  Demo Complete — All 5 healthcare agents demonstrated{RESET}")
    print(f"{BOLD}{'═'*65}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(run_demo())