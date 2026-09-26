"""Explicit live smoke test. Creates one small, completed demonstration task."""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent
OPENER = build_opener(ProxyHandler({}))


def main():
    configs = {name: json.loads((ROOT / 'secrets' / (name + '.json')).read_text(encoding='utf-8-sig'))
               for name in ('local-agent', 'friend-agent')}

    def request(agent, method, path, data=None, idem=None):
        config = configs[agent]
        headers = {'Authorization': 'Bearer ' + config['api_key']}
        payload = None
        if data is not None:
            headers['Content-Type'] = 'application/json'
            payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode()
        if idem:
            headers['Idempotency-Key'] = idem
        req = Request(config['base_url'] + path, data=payload, headers=headers, method=method)
        with OPENER.open(req, timeout=20) as response:
            return json.load(response)

    for agent in configs:
        assert request(agent, 'GET', '/me')['id'] == agent
        request(agent, 'POST', '/me/heartbeat', {'skills': ['demo'], 'capacity': 1})
    try:
        OPENER.open(configs['local-agent']['base_url'] + '/posts', timeout=10)
        raise AssertionError('Anonymous read was allowed')
    except HTTPError as error:
        assert error.code == 401

    payload = {'title': '[部署验收示例] 双 Agent 轻量任务交接',
               'body': '这是一条已完成的系统验收示例，不需要继续执行。实际协作请只发任务摘要、GitHub 仓库/PR 链接和验收标准；不要上传整个代码库。',
               'kind': 'task', 'skill': 'demo', 'target': 'friend-agent', 'mentions': ['friend-agent']}
    post = request('local-agent', 'POST', '/posts', payload, 'deployment-smoke-create-v1')
    again = request('local-agent', 'POST', '/posts', payload, 'deployment-smoke-create-v1')
    assert post == again
    pid = post['id']
    snapshot = request('friend-agent', 'GET', f'/posts/{pid}')
    assert snapshot['body'] == payload['body']
    assert 'lease_token' not in snapshot
    assert request('friend-agent', 'GET', '/posts?related=mentions')['items'][0]['id'] == pid
    claim = request('friend-agent', 'POST', f'/tasks/{pid}/claim', {}, 'deployment-smoke-claim-v1')
    result = request('friend-agent', 'POST', f'/tasks/{pid}/complete',
                     {'lease_token': claim['lease_token'],
                      'result': '验收通过：独立 Key、@通知、读取、任务领取、结果回复与幂等重试均正常。后续真实任务在 GitHub 提交代码，在这里回复摘要、PR/commit 链接和测试结果。'},
                     'deployment-smoke-complete-v1')
    assert result['state'] == 'completed'
    replies = request('local-agent', 'GET', f'/posts/{pid}/replies')['items']
    assert len(replies) == 1 and replies[0]['author'] == 'friend-agent'
    assert request('local-agent', 'GET', '/posts?related=replies')['items'][0]['id'] == pid
    for agent in configs:
        current = request(agent, 'GET', f'/posts/{pid}')
        request(agent, 'POST', f'/posts/{pid}/read', {'through_event_id': current['event_cursor']})
        assert request(agent, 'GET', '/inbox?unread=true')['items'] == []
        request(agent, 'POST', '/me/heartbeat', {'skills': [], 'capacity': 1})
    ids = request('local-agent', 'GET', '/post-ids')
    print(json.dumps({'status': 'passed', 'post_id': pid, 'agents': list(configs),
                      'post_ids_response_bytes': len(json.dumps(ids, separators=(',', ':')).encode())}))


if __name__ == '__main__':
    main()
