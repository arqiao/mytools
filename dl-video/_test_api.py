#!/usr/bin/env python3
"""临时测试脚本：验证方舟 Coding Plan API 连通性"""
import sys, requests
sys.path.insert(0, '/Volumes/DATADRIVE/workspace/sys')
from secrets_loader import load

creds = load('gkeys', 'gfeishu', 'gtokens')
api_key = creds.get('volcengine', {}).get('api_key', '')
print(f'API Key found: {bool(api_key)}, length: {len(api_key)}')

url = 'https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions'
headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {api_key}',
}
payload = {
    'model': 'doubao-seed-2.0-pro',
    'messages': [{'role': 'user', 'content': '你好，请用一句话回复'}],
    'max_tokens': 50,
}

try:
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    print(f'Status: {resp.status_code}')
    if resp.status_code == 200:
        data = resp.json()
        content = data.get('choices', [{}])[0].get('message', {}).get('content', '')
        print(f'Reply: {content}')
    else:
        print(f'Error: {resp.text[:500]}')
except Exception as e:
    print(f'Exception: {e}')
