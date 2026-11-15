"""Regression tests runnable without a Home Assistant installation."""
import ast
import base64
import datetime
import json
import logging
from pathlib import Path
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'aula'


def load_function(filename, name, namespace):
    tree = ast.parse((ROOT / filename).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), filename, 'exec'), namespace)
    return namespace[name]


class WidgetRegressions(unittest.TestCase):
    def setUp(self):
        self.ns = dict(base64=base64, json=json)
        self.guardian = load_function('widget_auth.py', 'widget_guardian', self.ns)
        claims = base64.urlsafe_b64encode(json.dumps({'sub': 'test-guardian'}).encode()).decode().rstrip('=')
        self.token = 'Bearer header.' + claims + '.signature'

    def test_widget_subject_and_invalid_tokens(self):
        self.assertEqual(self.guardian(self.token), 'test-guardian')
        for value in ['bad', None, 'Bearer a.e30.c']:
            with self.assertRaises(ValueError):
                self.guardian(value)

    def test_library_request_and_null_fields(self):
        source = (ROOT / 'client.py').read_text()
        block = source[source.index('        # Bibliotek:'):source.index('        # End of bibliotek')]
        namespace = dict(widget_guardian=self.guardian, CICERO_API='https://example.invalid', _LOGGER=logging.getLogger(__name__))
        exec('def fetch(self):\n' + block, namespace)
        captured = []
        response = SimpleNamespace(status_code=200, json=lambda: {'loans': [dict(title='Test book', author=None, dueDate='2027-01-01', numberOfLoans=1, coverImageUrl=None, patronDisplayName='Child One')]})
        def get(url, **kwargs):
            captured.append((url, kwargs))
            return response
        client = SimpleNamespace(_bibliotek=True, _institution_types={1:'school'}, widgets={'0019':'Library'}, get_token=lambda _:self.token, _session=SimpleNamespace(get=get), _institutionProfiles=['school'], _childuserids=['child'], _childnames={1:'Child One'})
        namespace['fetch'](client)
        self.assertTrue(client.library_available)
        self.assertIn('sessionUUID=test-guardian', captured[0][0])
        self.assertEqual(captured[0][1]['timeout'], 30)
        self.assertEqual(client.loaned_books['Child One'][0]['Author'], '')
        self.assertEqual(client.loaned_books['Child One'][0]['Cover'], '')
        response.status_code = 503
        namespace['fetch'](client)
        self.assertFalse(client.library_available)

    def test_courses_use_subject_and_all_institutions(self):
        captured = []
        response = SimpleNamespace(raise_for_status=lambda:None, json=lambda:{'personer':[{'navn':'Child One','institutioner':[{'forloeb':[{'navn':'Course A'}]},{'forloeb':[{'navn':'Course B'}]}]},{'navn':'Child Two','institutioner':None}]})
        def get(url, **kwargs):
            captured.append(kwargs)
            return response
        # No detail URL: summaries must work even without description pages.
        session = SimpleNamespace(get=get)
        namespace = dict(MIN_UDDANNELSE_API='https://example.invalid', widget_guardian=self.guardian)
        fetch = load_function('minuddannelse.py', 'forloeb', namespace)
        data = fetch(None, session, self.token, '2026-W46', ['child'], ['school'], 'unused')
        self.assertEqual(captured[0]['params']['sessionUUID'], 'test-guardian')
        self.assertEqual([c['Name'] for c in data['Child One']], ['Course A','Course B'])
        self.assertEqual(data['Child Two'], [])

    def test_next_week_is_not_current_week(self):
        tree = ast.parse((ROOT / 'sensors/education.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AulaEducationSensor')
        node = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'extra_state_attributes')
        node.decorator_list = []
        namespace = {}
        exec(compile(ast.Module(body=[node],type_ignores=[]), '<education>', 'exec'),namespace)
        obj = SimpleNamespace(_client=SimpleNamespace(forloebthisweek={'Child One':['current']},forloebnext={'Child One':['next']}),_child={'name':'Child One'})
        attrs = namespace['extra_state_attributes'](obj)
        self.assertEqual(attrs['course_this_week'], ['current'])
        self.assertEqual(attrs['course_next_week'], ['next'])

    def test_posts_and_mail_build_authenticated_urls(self):
        for filename, method in [('posts.py','_get_posts'),('mail.py','_get_mail')]:
            with self.subTest(filename=filename):
                requests_seen = []
                def make_url(method, **params):
                    return 'https://example.invalid/?method=' + method + '&access_token=synthetic'
                def get(url, **kwargs):
                    requests_seen.append(url)
                    return SimpleNamespace(status_code=200,json=lambda:{'status':{'message':'OK'},'data':{'posts':[],'threads':[]}})
                obj = SimpleNamespace(_session=SimpleNamespace(get=get),_institutionProfileIdsList=['1'],_auth_headers=lambda:{},_make_api_url=make_url)
                namespace = {'_LOGGER':logging.getLogger(__name__)}
                fetch = load_function(filename, method, namespace)
                fetch(obj)
                self.assertEqual(len(requests_seen),1)
                self.assertIn('access_token=synthetic',requests_seen[0])


if __name__ == '__main__':
    unittest.main()
