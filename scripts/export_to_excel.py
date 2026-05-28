import json
import pandas as pd
import os

def export_to_excel():
    json_path = os.path.join("reports", "stress_test_data.json")
    excel_path = os.path.join("reports", "stress_test_report.xlsx")
    
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found.")
        return
        
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    rows = []
    for item in data:
        row = {
            "Test ID": item.get("id"),
            "Role": item.get("role"),
            "Domain": item.get("domain"),
            "Category": item.get("category"),
            "Prompt": item.get("prompt"),
            "Expected Denial": item.get("expect_denial"),
            "Consistency Score": item.get("consistency_score"),
            "Accuracy Score": item.get("accuracy_score"),
            "Completeness Score": item.get("completeness_score"),
            "Overall Score": item.get("overall_score"),
            "Verdict": item.get("overall_verdict")
        }
        
        runs = item.get("runs", [])
        for i, run in enumerate(runs):
            row[f"Run {i+1} Status"] = run.get("status")
            row[f"Run {i+1} Latency (s)"] = run.get("latency_s")
            # Truncating content to prevent huge Excel cells, taking first 300 chars
            content = run.get("content", "")
            if len(content) > 300:
                content = content[:297] + "..."
            row[f"Run {i+1} Response Snippet"] = content
            
        rows.append(row)
        
    df = pd.DataFrame(rows)
    df.to_excel(excel_path, index=False)
    print(f"Successfully exported data to {excel_path}")

if __name__ == "__main__":
    export_to_excel()
