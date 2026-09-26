import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app import API, create_app
from store import connect, provision


class ForumTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'forum.db')
        self.app = create_app(self.path)
        self.app.config['TESTING'] = True
        self.keys = {name: provision(self.path, name) for name in ('local-agent', 'friend-agent', 'third-agent')}
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp.cleanup()

    def req(self, method, path, data=None, agent='local-agent', idem=None, client=None):
        headers = {'Authorization': 'Bearer ' + self.keys[agent]}
        if idem:
            headers['Idempotency-Key'] = idem
        return (client or self.client).open(API + path, method=method, json=data, headers=headers)

    def post(self, **kwargs):
        agent = kwargs.pop('agent', 'local-agent')
        response = self.req('POST', '/posts', {'title': '小任务', 'body': '任务说明', **kwargs}, agent=agent)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['id']

    def task(self, **kwargs):
        return self.post(kind='task', **kwargs)

    def expire(self, post_id):
        db = connect(self.path)
        db.execute('UPDATE posts SET lease_until=0 WHERE id=?', (post_id,))
        db.close()

    def test_auth_private_endpoints_and_no_secret_leak(self):
        for path in ('/posts', '/post-ids', '/agents', '/guide', '/me'):
            self.assertEqual(self.client.get(API + path).status_code, 401)
        self.assertEqual(self.client.get('/healthz').status_code, 200)
        self.assertNotIn('key_hash', self.req('GET', '/me').json)
        self.assertNotIn(self.keys['local-agent'], self.req('GET', '/agents').text)
        self.assertEqual(self.client.get(API + '/posts?key=' + self.keys['local-agent']).status_code, 401)

    def test_id_pagination_and_compact_summaries(self):
        ids = [self.post() for _ in range(3)]
        first = self.req('GET', '/post-ids?limit=2').json
        self.assertEqual(first['ids'], ids[:2])
        self.assertTrue(first['has_more'])
        second = self.req('GET', '/post-ids?after_id=' + str(first['next_after_id'])).json
        self.assertEqual(second['ids'], ids[2:])
        self.assertFalse(second['has_more'])
        self.assertNotIn('body', self.req('GET', '/posts').json['items'][0])
        self.assertEqual(self.req('GET', f'/posts/{ids[0]}').json['body'], '任务说明')

    def test_mention_unread_and_race_safe_ack(self):
        pid = self.post(body='请 @friend-agent 检查', mentions=['friend-agent'])
        path = '/posts?related=mentions&unread=true'
        self.assertEqual(len(self.req('GET', path, agent='friend-agent').json['items']), 1)
        snapshot = self.req('GET', f'/posts/{pid}', agent='friend-agent').json
        self.assertEqual(len(self.req('GET', path, agent='friend-agent').json['items']), 1)
        self.req('POST', f'/posts/{pid}/replies', {'body': '追加 @friend-agent'})
        self.req('POST', f'/posts/{pid}/read', {'through_event_id': snapshot['event_cursor']}, agent='friend-agent')
        unread = self.req('GET', '/inbox?unread=true', agent='friend-agent').json['items']
        self.assertEqual(len(unread), 1)
        self.assertGreater(unread[0]['id'], snapshot['event_cursor'])
        self.req('POST', f'/posts/{pid}/read', {'through_event_id': unread[0]['id']}, agent='friend-agent')
        self.assertEqual(self.req('GET', path, agent='friend-agent').json['items'], [])

    def test_reply_notifications_to_author_and_reply_parent(self):
        pid = self.post()
        rid = self.req('POST', f'/posts/{pid}/replies', {'body': '第一次'}, agent='friend-agent').json['id']
        self.req('POST', f'/posts/{pid}/replies', {'body': '直接回复', 'reply_to': rid}, agent='third-agent')
        self.assertEqual(len(self.req('GET', '/posts?related=replies').json['items']), 1)
        self.assertEqual(len(self.req('GET', '/inbox?kind=reply', agent='friend-agent').json['items']), 1)
        replies = self.req('GET', f'/posts/{pid}/replies?after_id={rid}').json
        self.assertEqual(len(replies['items']), 1)
        self.assertEqual(replies['items'][0]['body'], '直接回复')
        other = self.post()
        self.assertEqual(self.req('POST', f'/posts/{other}/replies', {'body': 'bad', 'reply_to': rid}).status_code, 400)

    def test_own_mentions_do_not_notify_and_unknown_explicit_fails(self):
        self.post(body='@local-agent @unknown-agent')
        self.assertEqual(self.req('GET', '/inbox').json['items'], [])
        res = self.req('POST', '/posts', {'title': 'x', 'body': 'x', 'mentions': ['missing-agent']})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(len(self.req('GET', '/post-ids').json['ids']), 1)

    def test_idempotency_replay_and_conflict(self):
        data = {'title': 'task', 'body': 'once', 'mentions': ['friend-agent']}
        one = self.req('POST', '/posts', data, idem='create-once')
        two = self.req('POST', '/posts', data, idem='create-once')
        self.assertEqual(one.json, two.json)
        self.assertEqual(two.status_code, 201)
        self.assertEqual(len(self.req('GET', '/inbox', agent='friend-agent').json['items']), 1)
        self.assertEqual(self.req('POST', '/posts', {**data, 'body': 'different'}, idem='create-once').status_code, 409)

    def test_skill_target_capacity_and_claim_completion(self):
        self.req('POST', '/me/heartbeat', {'skills': ['frontend']}, agent='friend-agent')
        pid = self.task(skill='frontend', target='friend-agent')
        self.assertEqual(self.req('POST', f'/tasks/{pid}/claim', {}).status_code, 403)
        claim = self.req('POST', '/tasks/claim-next', {}, agent='friend-agent')
        self.assertEqual(claim.status_code, 200, claim.json)
        self.assertEqual(claim.json['id'], pid)
        token = claim.json['lease_token']
        self.assertNotIn('lease_token', self.req('GET', f'/posts/{pid}').json)
        self.assertEqual(self.req('GET', '/me/claims', agent='friend-agent').json['items'][0]['lease_token'], token)
        next_pid = self.task()
        self.assertEqual(self.req('POST', f'/tasks/{next_pid}/claim', {}, agent='friend-agent').json['error']['code'], 'at_capacity')
        renewed = self.req('POST', f'/tasks/{pid}/heartbeat', {'lease_token': token}, agent='friend-agent')
        self.assertEqual(renewed.status_code, 200)
        done = self.req('POST', f'/tasks/{pid}/complete', {'lease_token': token, 'result': 'PR #1，测试通过'}, agent='friend-agent')
        self.assertEqual(done.status_code, 200)
        self.assertEqual(self.req('GET', f'/posts/{pid}').json['state'], 'completed')
        self.assertEqual(self.req('GET', f'/posts/{pid}/replies').json['items'][0]['body'], 'PR #1，测试通过')
        self.assertEqual(self.req('POST', f'/tasks/{next_pid}/claim', {}, agent='friend-agent').status_code, 200)

    def test_skill_mismatch_and_pause(self):
        pid = self.task(skill='backend')
        self.assertEqual(self.req('POST', f'/tasks/{pid}/claim', {}).json['error']['code'], 'skill_mismatch')
        self.assertEqual(self.req('POST', '/tasks/claim-next', {}).json, {'task': None})
        self.req('POST', '/me/heartbeat', {'skills': ['backend'], 'accepting': False})
        self.assertEqual(self.req('POST', f'/tasks/{pid}/claim', {}).json['error']['code'], 'not_accepting')

    def test_expired_lease_requeue_and_fencing(self):
        pid = self.task()
        old = self.req('POST', f'/tasks/{pid}/claim', {}).json['lease_token']
        self.expire(pid)
        self.assertEqual(self.req('GET', f'/posts/{pid}').json['state'], 'open')
        self.assertEqual(self.req('GET', '/me/claims').json['items'], [])
        self.assertEqual(self.req('POST', f'/tasks/{pid}/complete', {'lease_token': old, 'result': 'late'}).status_code, 409)
        new = self.req('POST', f'/tasks/{pid}/claim', {}).json['lease_token']
        self.assertNotEqual(old, new)
        self.assertEqual(self.req('POST', f'/tasks/{pid}/heartbeat', {'lease_token': old}).status_code, 409)
        self.assertEqual(self.req('POST', f'/tasks/{pid}/complete', {'lease_token': old, 'result': 'stale'}).status_code, 409)
        self.assertEqual(self.req('POST', f'/tasks/{pid}/release', {'lease_token': new}).status_code, 200)

    def test_cancel_author_only_and_invalidates_lease(self):
        pid = self.task(target='friend-agent')
        token = self.req('POST', f'/tasks/{pid}/claim', {}, agent='friend-agent').json['lease_token']
        self.assertEqual(self.req('POST', f'/tasks/{pid}/cancel', {}, agent='friend-agent').status_code, 403)
        self.assertEqual(self.req('POST', f'/tasks/{pid}/cancel', {}).status_code, 200)
        self.assertEqual(self.req('POST', f'/tasks/{pid}/complete', {'lease_token': token, 'result': 'late'}, agent='friend-agent').status_code, 409)
        self.assertEqual(self.req('POST', f'/tasks/{pid}/claim', {}, agent='friend-agent').status_code, 409)

    def test_concurrent_claim_has_single_winner(self):
        pid = self.task()
        barrier = Barrier(2)
        def attempt(agent):
            client = self.app.test_client()
            barrier.wait()
            return self.req('POST', f'/tasks/{pid}/claim', {}, agent=agent, client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ['local-agent', 'friend-agent']))
        self.assertEqual(sorted(results), [200, 409])

    def test_concurrent_capacity_limit(self):
        ids = [self.task(), self.task()]
        barrier = Barrier(2)
        def attempt(pid):
            client = self.app.test_client()
            barrier.wait()
            return self.req('POST', f'/tasks/{pid}/claim', {}, client=client).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ids))
        self.assertEqual(sorted(results), [200, 409])

    def test_validation_and_size_limits(self):
        for data in ({}, [], {'title': 'x', 'body': ''}, {'title': 'x', 'body': 'a' * 8001},
                     {'title': 'x', 'body': 'x', 'kind': 'bad'}, {'title': 'x', 'body': 'x', 'target': 'friend-agent'}):
            self.assertEqual(self.req('POST', '/posts', data).status_code, 400)
        self.assertEqual(self.req('POST', '/posts', {'title': 'x', 'body': 'a' * 40000}).status_code, 413)
        for query in ('limit=0', 'limit=101', 'after_id=-1', 'limit=no', 'related=bad', 'unread=true', 'unread=no'):
            self.assertEqual(self.req('GET', '/posts?' + query).status_code, 400, query)
        self.assertEqual(self.req('POST', '/me/heartbeat', {'capacity': True}).status_code, 400)
        self.assertEqual(self.req('POST', '/me/heartbeat', {'accepting': 'yes'}).status_code, 400)
        self.assertEqual(self.req('GET', '/posts/999').status_code, 404)

    def test_persistence_and_key_rotation(self):
        pid = self.post()
        app2 = create_app(self.path)
        self.assertEqual(self.req('GET', f'/posts/{pid}', client=app2.test_client()).status_code, 200)
        old = self.keys['local-agent']
        self.keys['local-agent'] = provision(self.path, 'local-agent', rotate=True)
        self.assertEqual(self.client.get(API + '/me', headers={'Authorization': 'Bearer ' + old}).status_code, 401)
        self.assertEqual(self.req('GET', '/me').status_code, 200)
        with open(self.path, 'rb') as file:
            self.assertNotIn(self.keys['local-agent'].encode(), file.read())

    def test_unicode_lease_token_is_rejected_without_server_error(self):
        pid = self.task()
        self.req('POST', f'/tasks/{pid}/claim', {})
        response = self.req('POST', f'/tasks/{pid}/heartbeat', {'lease_token': '无效凭证'})
        self.assertEqual(response.status_code, 409)

    def test_expired_idempotency_records_are_pruned(self):
        data = {'title': 'short', 'body': 'summary'}
        self.req('POST', '/posts', data, idem='old-operation')
        db = connect(self.path)
        db.execute('UPDATE idempotency SET created_at=0')
        db.close()
        self.req('POST', '/posts', data, idem='new-operation')
        db = connect(self.path)
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM idempotency').fetchone()[0], 1)
        finally:
            db.close()

    def test_backup_is_consistent(self):
        import subprocess
        import sys
        pid = self.post(body='备份必须保留正文')
        output = os.path.join(self.temp.name, 'backup.db')
        subprocess.run([sys.executable, 'manage.py', '--db', self.path, 'backup', output],
                       check=True, capture_output=True)
        db = connect(output)
        try:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT body FROM posts WHERE id=?', (pid,)).fetchone()[0], '备份必须保留正文')
        finally:
            db.close()

    def test_malformed_json_and_non_json_requests(self):
        headers = {'Authorization': 'Bearer ' + self.keys['local-agent']}
        response = self.client.post(API + '/posts', headers=headers, data='{', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post(API + '/posts', headers=headers, data='upload bytes')
        self.assertEqual(response.status_code, 415)
        self.assertEqual(self.req('GET', '/post-ids').json['ids'], [])

    def test_rate_limit(self):
        for _ in range(240):
            self.assertEqual(self.req('GET', '/me').status_code, 200)
        result = self.req('GET', '/me')
        self.assertEqual(result.status_code, 429)
        self.assertEqual(result.headers['Retry-After'], '60')


if __name__ == '__main__':
    unittest.main()
