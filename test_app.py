"""Offline regression tests; no API credentials or billable requests."""
import unittest
from unittest.mock import patch
import urllib.error
import app


class ModelBrowserTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {'id': 'anthropic/claude-paid', 'name': 'Claude Paid', 'context_length': 100,
             'supported_parameters': ['tools'], 'pricing': {'prompt': '0.000003', 'completion': '0.000015'}},
            {'id': 'anthropic/claude-paid:batch', 'pricing': {'prompt': '0', 'completion': '0'}},
            {'id': 'google/gemini-free', 'context_length': 200, 'supported_parameters': ['tools'],
             'pricing': {'prompt': '0', 'completion': '0', 'request': '0'}},
            {'id': 'other/unknown', 'context_length': 300},
            {'id': 'other/request-fee', 'pricing': {'prompt': '0', 'completion': '0', 'request': '0.01'}}]

    def test_batch_exclusion(self):
        self.assertEqual([r['id'] for r in app.filter_models(self.rows)], ['anthropic/claude-paid'])
        with self.assertRaises(ValueError):
            app.launch_env('fake', 'anthropic/claude-paid:batch', {})

    def test_provider_free_tools(self):
        rows = app.filter_models(self.rows, claude_only=False, provider='google', free_only=True, tools_only=True)
        self.assertEqual([r['id'] for r in rows], ['google/gemini-free'])
        self.assertEqual(len(app.filter_models(self.rows, claude_only=False, free_only=True)), 1)

    def test_favorites_recent_and_search(self):
        self.assertEqual(len(app.filter_models(self.rows, claude_only=False, favorites=['other/unknown'], view='Favorites')), 1)
        rows = app.filter_models(self.rows, claude_only=False, view='Recent', recent=['google/gemini-free', 'anthropic/claude-paid'])
        self.assertEqual(rows[0]['id'], 'google/gemini-free')
        self.assertEqual(len(app.filter_models(self.rows, 'CLAUDE PAID')), 1)

    def test_sort_and_bad_prices(self):
        rows = app.filter_models(self.rows, claude_only=False, sort='Context: high to low')
        self.assertEqual(rows[0]['id'], 'other/unknown')
        rows = app.filter_models(self.rows, claude_only=False, sort='Input price: low to high')
        self.assertEqual(rows[-1]['id'], 'other/unknown')
        self.assertEqual(app.model_price('NaN'), 'N/A')
        self.assertEqual(app.model_price('0.000003'), '$3.00')


class OllamaTests(unittest.TestCase):
    def test_normalize_ollama_models(self):
        rows = app.normalize_ollama_models({'models': [
            {'name': 'llama3.1:8b', 'modified_at': 'today', 'size': 123, 'details': {'family': 'llama'}},
            {'name': 'qwen2.5-coder:latest'}]})
        self.assertEqual([r['id'] for r in rows], ['llama3.1:8b', 'qwen2.5-coder:latest'])
        self.assertEqual(rows[0]['provider'], 'ollama')
        self.assertEqual(app.catalog_provider(rows[0]), 'ollama')

    def test_ollama_launch_env(self):
        env = app.launch_env('', 'llama3.1:8b', {'ANTHROPIC_AUTH_TOKEN': 'old', 'OPENROUTER_API_KEY': 'old'},
                             provider=app.PROVIDER_OLLAMA, base_url='http://localhost:11434/')
        self.assertEqual(env['ANTHROPIC_BASE_URL'], 'http://localhost:11434')
        self.assertEqual(env['ANTHROPIC_AUTH_TOKEN'], 'ollama')
        self.assertEqual(env['ANTHROPIC_MODEL'], 'llama3.1:8b')
        self.assertNotIn('OPENROUTER_API_KEY', env)

    def test_base_url_validation(self):
        with self.assertRaises(ValueError):
            app.normalize_base_url('localhost:11434')
        self.assertEqual(app.normalize_base_url('http://localhost:11434/'), 'http://localhost:11434')


class ProbeTests(unittest.TestCase):
    def fake_api(self, token, path, body=None):
        self.assertEqual(path, 'messages')
        self.assertLessEqual(body['max_tokens'], 256)
        if 'tools' not in body:
            return {'content': [{'type': 'text', 'text': 'CHECK_OK'}]}
        if body['tool_choice']['type'] == 'tool':
            nonce = body['tools'][0]['input_schema']['properties']['token']['enum'][0]
            return {'content': [{'type': 'tool_use', 'id': 'tool-test', 'name': 'compatibility_ping', 'input': {'token': nonce}}]}
        result = body['messages'][-1]['content'][0]
        self.assertEqual(result['tool_use_id'], 'tool-test')
        return {'content': [{'type': 'text', 'text': result['content']}]}

    def test_complete_tool_round_trip(self):
        with patch.object(app, 'api_request', side_effect=self.fake_api) as mock:
            result = app.probe_model('fake', 'google/gemini-test')
        self.assertEqual(result['status'], 'Passed')
        self.assertEqual(result['round_trip'], 'Passed')
        self.assertEqual(mock.call_count, 3)

    def test_response_failure(self):
        with patch.object(app, 'api_request', side_effect=RuntimeError('HTTP 402: Credit or spending limit reached')):
            result = app.probe_model('fake', 'test/model')
        self.assertEqual(result['status'], 'Failed')
        self.assertEqual(result['response'], 'Failed')
        self.assertEqual(result['tools'], 'Untested')

    def test_tool_failure(self):
        with patch.object(app, 'api_request', side_effect=[{'content': [{'type': 'text', 'text': 'OK'}]}, {'content': []}]):
            result = app.probe_model('fake', 'test/model')
        self.assertEqual(result['response'], 'Passed')
        self.assertEqual(result['tools'], 'Failed')
        self.assertEqual(result['round_trip'], 'Untested')

    def test_round_trip_failure(self):
        def fake(token, path, body):
            if body.get('tool_choice', {}).get('type') == 'none':
                return {'content': [{'type': 'text', 'text': 'Ignored tool result'}]}
            return self.fake_api(token, path, body)
        with patch.object(app, 'api_request', side_effect=fake):
            result = app.probe_model('fake', 'test/model')
        self.assertEqual(result['round_trip'], 'Failed')

    def test_api_error_redaction(self):
        error = urllib.error.HTTPError('url', 401, 'secret-value', {}, None)
        with patch.object(app.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaises(RuntimeError) as context:
                app.api_request('fake', 'key')
        self.assertNotIn('secret-value', str(context.exception))
        self.assertIn('401', str(context.exception))


class SpendingTests(unittest.TestCase):
    def test_thresholds_and_missing_values(self):
        limits = {'daily': '5', 'monthly': '50', 'remaining': '10'}
        self.assertEqual(len(app.spending_alerts({'usage_daily': 5, 'usage_monthly': 51, 'limit_remaining': 9}, limits)), 3)
        self.assertEqual(app.spending_alerts({'usage_daily': 4, 'usage_monthly': 20, 'limit_remaining': None}, limits), [])
        self.assertEqual(app.spending_alerts({'usage_daily': 500}, {'daily': '0'}), [])
        self.assertEqual(app.spending_alerts({}, limits), [])

    def test_currency(self):
        self.assertEqual(app.money('1234.5'), '$1,234.50')
        self.assertEqual(app.money(None), 'Unavailable')
        self.assertEqual(app.money('NaN'), 'Unavailable')

    def test_key_revision_invalidates_test(self):
        item = {'id': 'test-key', 'secret': 'encrypted-first'}
        class Stub:
            data = {'active': 'test-key', 'keys': [item], 'model_tests': {}}
            credential_revision = app.Manager.credential_revision
        stub = Stub()
        stub.data['model_tests']['test-key'] = {'test/model': {'status': 'Passed', 'revision': stub.credential_revision(item)}}
        self.assertEqual(app.Manager.test_result(stub, 'test/model')['status'], 'Passed')
        item['secret'] = 'encrypted-replacement'
        self.assertIsNone(app.Manager.test_result(stub, 'test/model'))


class UsageSnapshotTests(unittest.TestCase):
    class Var:
        def __init__(self, value=''):
            self.value = value
        def get(self):
            return self.value
        def set(self, value):
            self.value = value

    def stub(self):
        import queue
        class Stub:
            active_key = app.Manager.active_key
            credential_revision = app.Manager.credential_revision
        stub = Stub()
        item = {'id': 'key', 'name': 'Test key', 'secret': 'encrypted'}
        stub.data = {'active': 'key', 'keys': [item]}
        stub.usage_busy = False
        stub.usage_text = self.Var()
        stub.usage_details = self.Var()
        stub.alert_vars = {name: self.Var() for name in ('daily', 'monthly', 'remaining')}
        stub.events = queue.Queue()
        stub.usage_by_key = {}
        return stub

    def test_usage_fetch_without_account_permission(self):
        class Thread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        stub = self.stub()
        with patch.object(app, 'protect', return_value='fake'), patch.object(app.threading, 'Thread', Thread), \
             patch.object(app, 'api_request', side_effect=[{'data': {'limit': None, 'limit_remaining': None, 'usage': 4}}, RuntimeError('HTTP 403')]):
            app.Manager.refresh_usage(stub)
        event = stub.events.get_nowait()
        self.assertEqual(event[0], 'usage')
        self.assertIsNone(event[3]['balance'])
        snapshot = event[3]
        snapshot['revision'] = event[2]
        stub.usage_by_key['key'] = snapshot
        app.Manager.render_usage(stub)
        self.assertIn('Account balance: Unavailable', stub.usage_details.get())
        self.assertIn('No cap configured', stub.usage_details.get())

    def test_key_change_hides_old_snapshot(self):
        stub = self.stub()
        stub.usage_by_key['key'] = {'revision': 'old revision', 'key': {}}
        app.Manager.render_usage(stub)
        self.assertIn('not refreshed', stub.usage_text.get())


if __name__ == '__main__':
    unittest.main()
