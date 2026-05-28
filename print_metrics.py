import json

with open("reports/stress_test_data.json", "r", encoding="utf-8") as f:
    data = json.load(f)

for item in data:
    latencies = [r["latency_s"] for r in item["runs"]]
    print(f"{item['id']} | {item['role']} | {item['domain']} | consistency={item.get('consistency_score')} | accuracy={item.get('accuracy_score')} | overall={item.get('overall_score')} | latencies={latencies}")
