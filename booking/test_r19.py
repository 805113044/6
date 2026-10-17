"""R19 regressions: protect both HTTPS POST functionality and CSRF strictness."""
import re
from django.test import Client, TestCase, override_settings
from django.conf import settings

@override_settings(ALLOWED_HOSTS=['testserver'], CSRF_TRUSTED_ORIGINS=[], SECURE_SSL_REDIRECT=False)
class R19CsrfLoginTests(TestCase):
    def test_login_policy_and_secure_post_behavior(self):
        self.assertEqual(settings.SECURE_REFERRER_POLICY, 'same-origin')
        client = Client(enforce_csrf_checks=True)
        page = client.get('/login/', secure=True)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers.get('Referrer-Policy'), 'same-origin')
        html = page.content.decode('utf-8')
        found = re.search(r'name="csrfmiddlewaretoken"\s+value="([a-zA-Z0-9]+)"', html)
        self.assertIsNotNone(found, 'Admin login form must retain its CSRF token')
        form = {
            'username': 'r19-no-such-user',
            'password': 'r19-invalid-test-password',
            'csrfmiddlewaretoken': found.group(1),
        }
        # No Origin: a legitimate HTTPS same-origin Referer must pass CSRF.
        accepted = client.post('/login/', form, secure=True,
                               HTTP_REFERER='https://testserver/login/')
        self.assertEqual(accepted.status_code, 200, 'Bad credentials must show the login form, not a CSRF 403')
        # Missing/wrong Referer must continue to be rejected (no CSRF bypass).
        self.assertEqual(client.post('/login/', form, secure=True).status_code, 403)
        self.assertEqual(client.post('/login/', form, secure=True,
                         HTTP_REFERER='https://untrusted.invalid/login/').status_code, 403)
