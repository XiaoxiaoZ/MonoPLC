import httpx, json
r = httpx.get('http://127.0.0.1:8000/api/logs?n=20')
logs = r.json()['logs']
print(f"Total: {len(logs)} logs")
for l in logs:
    print(f"{l['e_type']:15s}  target={l['target']:20s}  {l['payload'][:65]}")
