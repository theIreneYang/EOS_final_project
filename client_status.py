#!/usr/bin/env python3
"""Preview recording status; does not access a microphone or submit audio."""
import argparse
import json
import time
from urllib.request import Request, urlopen
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--client-id', choices=['A','B'], required=True)
p.add_argument('--seconds', type=float, default=10)
p.add_argument('--server', default='http://192.168.222.222:8000')
a=p.parse_args()
if not 0 < a.seconds <= 120:
    p.error('--seconds must be greater than 0 and at most 120')
def send(state):
    body=json.dumps({'client_id': a.client_id, 'state': state}).encode()
    with urlopen(Request(a.server.rstrip('/')+'/api/client-state',data=body,headers={'Content-Type':'application/json'}),timeout=4) as r:
        if not json.load(r).get('ok'): raise RuntimeError('Status rejected')
print('收音狀態預覽（沒有錄音、沒有上傳音檔）。Ctrl+C 結束。', flush=True)
try:
    end=time.monotonic()+a.seconds
    while time.monotonic()<end:
        send('recording')
        time.sleep(min(1.5,max(0,end-time.monotonic())))
except KeyboardInterrupt:
    pass
finally:
    try: send('idle')
    except Exception as exc: print('無法清除狀態，Server 將於 5 秒內自動清除：',exc)
