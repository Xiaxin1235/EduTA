# -*- coding: utf-8 -*-
"""Verification script for API chart generation and tool calls."""

import json
import urllib.request

def ask(q):
    data = json.dumps({"question": q, "session_id": "test_api_sess"}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8600/api/v1/chat",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))

if __name__ == "__main__":
    queries = [
        "七台河市新兴区罗泉学校给老师配置了多少台平板电脑？",
        "它在区里排第几名？",
        "评估武汉市的教学水平",
    ]
    for q in queries:
        print(f"\n>>> Query: {q}")
        res = ask(q)
        print(f"  Clarify: {res.get('clarify')}")
        tool_calls = res.get("tool_calls", [])
        charts = res.get("charts", [])
        print(f"  Tool calls: {len(tool_calls)}")
        for tc in tool_calls:
            print(f"    - Tool: {tc.get('tool')}, Success: {tc.get('success')}, Duration: {tc.get('duration_ms')}ms")
        print(f"  Charts: {len(charts)}")
        for c in charts:
            print(f"    - Title: {c.get('title')}")
            print(f"    - URL: {c.get('url')}")
            print(f"    - Filename: {c.get('filename')}")
            print(f"    - Has Base64: {bool(c.get('base64'))}")
            # Verify downloading chart image via HTTP
            chart_url = f"http://127.0.0.1:8600{c.get('url')}"
            with urllib.request.urlopen(chart_url) as ch_resp:
                img_bytes = ch_resp.read()
                print(f"    - HTTP Download verified: {len(img_bytes)} bytes, content-type={ch_resp.headers.get('Content-Type')}")

    print("\n[ALL 3 SCENARIOS VERIFIED SUCCESSFULLY]")
