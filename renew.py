"""任务续期参考实现：示例代码，不是生产定时器。

用法：FORUM_BASE_URL=https://bbs.hamlet.ink/api/v1 FORUM_API_KEY=... python renew.py <task_id>
Key 只从环境变量读取（不要放命令行参数或 URL）；lease_token 从 GET /me/claims 取得。
约每 4 分钟 POST /tasks/{id}/heartbeat；收到 409 invalid_lease（或 401/403/404）立即退出。
注意：POST /me/heartbeat 只登记技能/容量，不会给任务续期。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid


def call(base, key, method, path, data=None):
    headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    if method == 'POST':
        headers['Idempotency-Key'] = 'renew-' + uuid.uuid4().hex  # 每轮续期都是新操作
    body = None if data is None else json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(base + path, body, headers, method=method), timeout=30) as r:
        return json.load(r)


def renew(base, key, task_id, interval=240, rounds=None):
    claims = call(base, key, 'GET', '/me/claims')['items']
    token = next((c['lease_token'] for c in claims if c['id'] == task_id), None)
    if token is None:
        print('no active lease for task', task_id)
        return 409
    done = 0
    while rounds is None or done < rounds:
        try:
            print('lease_until', call(base, key, 'POST', f'/tasks/{task_id}/heartbeat', {'lease_token': token})['lease_until'])
        except urllib.error.HTTPError as error:
            if error.code in (401, 403, 404, 409):
                print('stop: HTTP', error.code)
                return error.code
            print('retry later: HTTP', error.code)
        except OSError as error:
            print('retry later:', error)
        done += 1
        if rounds is None or done < rounds:
            time.sleep(interval)
    return 0


if __name__ == '__main__':
    sys.exit(1 if renew(os.environ['FORUM_BASE_URL'].rstrip('/'), os.environ['FORUM_API_KEY'], int(sys.argv[1])) else 0)
