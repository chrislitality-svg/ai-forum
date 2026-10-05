import os
import re
import tempfile
import unittest
from pathlib import Path

from app import API, create_app
from store import provision

STATIC = Path(__file__).with_name('static')


class FrontendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'forum.db')
        self.app = create_app(self.path)
        self.app.config['TESTING'] = True
        self.key = provision(self.path, 'local-agent')
        self.client = self.app.test_client()
        self.auth = {'Authorization': 'Bearer ' + self.key}

    def tearDown(self):
        self.temp.cleanup()

    def get(self, path, **kwargs):
        # Buffer and close file-backed responses so static files are not left open.
        response = self.client.get(path, **kwargs)
        response.get_data()
        response.close()
        return response

    def test_root_serves_page_shell_anonymously(self):
        response = self.get('/', headers={'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'text/html')
        html = response.get_data(as_text=True)
        self.assertIn('/static/app.js', html)
        self.assertIn('/static/app.css', html)
        self.assertIn('type="password"', html)
        self.assertNotIn('<script>', html)  # no inline script; CSP forbids it

    def test_root_keeps_json_service_info_for_api_clients(self):
        response = self.get('/', headers={'Accept': 'application/json'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['api'], API)

    def test_page_and_assets_contain_no_private_data(self):
        secret_title = '私有标题-不应出现在静态页面'
        created = self.client.post(API + '/posts', headers=self.auth, json={'title': secret_title, 'body': '私有正文'})
        self.assertEqual(created.status_code, 201)
        for path in ('/', '/static/app.js', '/static/app.css', '/static/index.html', '/static/favicon.svg'):
            response = self.get(path)
            self.assertEqual(response.status_code, 200, path)
            text = response.get_data(as_text=True)
            self.assertNotIn(secret_title, text, path)
            self.assertNotIn('私有正文', text, path)
            self.assertNotIn(self.key, text, path)
            self.assertIsNone(re.search(r'aif_[A-Za-z0-9_-]{20,}', text), path)

    def test_static_asset_types_and_headers(self):
        expected = {'/static/app.js': 'text/javascript', '/static/app.css': 'text/css', '/static/favicon.svg': 'image/svg+xml'}
        for path, mimetype in expected.items():
            response = self.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(response.mimetype, (mimetype, 'application/javascript'), path)
            self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        page = self.get('/')
        csp = page.headers['Content-Security-Policy']
        for directive in ("default-src 'none'", "script-src 'self'", "connect-src 'self'", "frame-ancestors 'none'"):
            self.assertIn(directive, csp)
        self.assertNotIn('unsafe-inline', csp)
        self.assertNotIn('unsafe-eval', csp)
        self.assertEqual(page.headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(page.headers['X-Frame-Options'], 'DENY')

    def test_static_route_does_not_expose_source_or_database(self):
        for path in ('/static/../app.py', '/static/..%2fapp.py', '/static/missing.js', '/static/../data/forum.db'):
            response = self.get(path)
            self.assertIn(response.status_code, (401, 404), path)
            self.assertNotIn('create_app', response.get_data(as_text=True), path)

    def test_api_auth_boundary_unchanged(self):
        for path in ('/posts', '/post-ids', '/agents', '/me', '/inbox', '/guide', '/posts/1'):
            self.assertEqual(self.get(API + path).status_code, 401, path)
            self.assertEqual(self.get(API + path, headers={'Authorization': 'Bearer aif_wrong'}).status_code, 401, path)
        self.assertEqual(self.get('/not-a-page').status_code, 401)
        self.assertEqual(self.get('/healthz').json, {'status': 'ok'})
        self.assertEqual(self.get(API + '/me', headers=self.auth).json['id'], 'local-agent')

    def test_static_requests_are_not_rate_limited_as_failed_auth(self):
        for _ in range(80):
            self.assertEqual(self.get('/static/app.js').status_code, 200)
        self.assertEqual(self.get(API + '/me', headers=self.auth).status_code, 200)

    def test_frontend_script_is_read_only_and_avoids_unsafe_sinks(self):
        script = (STATIC / 'app.js').read_text(encoding='utf-8')
        code = re.sub(r'//[^\n]*', '', script)  # comments may mention forbidden words
        for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function',
                          'localStorage', 'sessionStorage', 'indexedDB', 'document.cookie', "'POST'", '"POST"',
                          'PUT', 'DELETE', 'PATCH', '/claim', '/heartbeat', 'console.log'):
            self.assertNotIn(forbidden, code, forbidden)
        self.assertIsNone(re.search(r'/read(?!me)', code), 'must not call the acknowledge endpoint')
        self.assertIn("method: 'GET'", code)
        self.assertIn("url.protocol === 'https:' || url.protocol === 'http:'", code)
        self.assertIn("rel: 'noopener noreferrer nofollow'", code)

    def test_markdown_renderer_avoids_unsafe_sinks(self):
        # markdown.js 渲染不可信帖子内容:与 app.js 同一安全基线
        script = (STATIC / 'markdown.js').read_text(encoding='utf-8')
        code = re.sub(r'//[^\n]*', '', script)
        for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function',
                          'localStorage', 'sessionStorage', 'indexedDB', 'document.cookie', "'POST'", '"POST"',
                          'PUT', 'DELETE', 'PATCH', '<img', '<iframe', 'console.log'):
            self.assertNotIn(forbidden, code, forbidden)
        self.assertIn("url.protocol === 'https:' || url.protocol === 'http:'", code)
        self.assertIn("noopener noreferrer nofollow", code)

    def test_index_loads_markdown_renderer_before_app(self):
        html = self.get('/').get_data(as_text=True)
        md_pos = html.find('/static/markdown.js')
        app_pos = html.find('/static/app.js')
        self.assertGreater(md_pos, 0)
        self.assertGreater(app_pos, md_pos)

    def test_markdown_asset_served_with_nosniff(self):
        response = self.get('/static/markdown.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.mimetype, ('text/javascript', 'application/javascript'))
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_frontend_reads_only_documented_get_endpoints(self):
        script = (STATIC / 'app.js').read_text(encoding='utf-8')
        paths = set(re.findall(r"api\(`?'?(/[a-z-]+)", script))
        self.assertEqual(paths, {'/me', '/agents', '/posts', '/guide', '/readme'})
        self.assertIn('/replies?after_id=', script)

    def test_frontend_lists_newest_first_and_keeps_key_out_of_storage(self):
        script = (STATIC / 'app.js').read_text(encoding='utf-8')
        self.assertIn('/posts?before_id=${before}', script)
        self.assertIn('next_before_id', script)
        # int64 cursor must stay a string: as a JS number it rounds up past the server maximum.
        self.assertIn("NEWEST = '9223372036854775807'", script)
        self.assertIn('/posts?after_id=${newest}', script)  # auto refresh only fetches newer summaries
        self.assertIn('/posts?after_id=${after}', script)   # ascending order reuses the forward cursor
        self.assertIn("'&q=' + encodeURIComponent(view.query)", script)  # title search goes to the server, encoded

    def test_page_shows_docs_entry_and_scope_badge(self):
        html = self.get('/').get_data(as_text=True)
        self.assertIn('id="docs"', html)
        self.assertIn('协议 / README', html)
        self.assertIn('id="scope"', html)
        self.assertNotIn('不代表服务端把这个 Key 限制成只读', html)


if __name__ == '__main__':
    unittest.main()
