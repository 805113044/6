"""R15 isolated regressions for private response and error-report surfaces."""
from django.test import SimpleTestCase, RequestFactory, override_settings
from django.conf import settings
from django.contrib.auth.hashers import make_password, check_password
from booking.middleware import PrivacyHeadersMiddleware
from booking.security_r15 import RedactedExceptionFilter
from django.http import HttpResponse

class R15SecurityTests(SimpleTestCase):
    def test_private_headers_and_denied_trace(self):
        middleware=PrivacyHeadersMiddleware(lambda req: HttpResponse('OK'))
        response=middleware(RequestFactory().get('/lookup/?q=private-id'))
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response['Referrer-Policy'],'same-origin')
        self.assertEqual(response['X-Robots-Tag'],'noindex, nofollow, noarchive')
        self.assertEqual(response['Cross-Origin-Resource-Policy'],'same-origin')
        denied=middleware(RequestFactory().generic('TRACE','/lookup/'))
        self.assertEqual(denied.status_code,405)
    def test_all_private_form_inputs_redacted(self):
        request=RequestFactory().post('/furniture/',{
            'student_id':'NONPUBLIC-ID', 'password':'rawpw', 'phone':'13300000000',
            'borrower_name':'student-test', 'loan_password_confirm':'rawpw',
            'item_name':'chair', 'item_quantity':'2',
        })
        protected=RedactedExceptionFilter().get_post_parameters(request)
        for key in ('student_id','password','phone','borrower_name','loan_password_confirm'):
            self.assertNotEqual(protected[key],request.POST[key])
        self.assertEqual(protected['item_name'],'chair')
    def test_old_and_new_hashes_are_backward_compatible(self):
        from django.contrib.auth.hashers import PBKDF2PasswordHasher
        secret='r15-hash-only-example'
        salted=make_password(secret)
        self.assertTrue(salted.startswith('pbkdf2_sha256_peppered_v1$'))
        self.assertTrue(check_password(secret,salted))
        self.assertFalse(check_password('wrong',salted))
        legacy=PBKDF2PasswordHasher().encode(secret,PBKDF2PasswordHasher().salt())
        self.assertTrue(check_password(secret,legacy))
