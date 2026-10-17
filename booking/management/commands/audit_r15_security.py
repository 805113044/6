"""Non-invasive database/security policy audit; prints only risk categories."""
import ipaddress
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.contrib.auth.hashers import make_password, check_password
from booking.models import Booking, FurnitureLoan

class Command(BaseCommand):
    help = 'R15 security checks: no configuration writes, no raw student rows or credentials output.'

    def handle(self, *args, **kwargs):
        failures, warnings = [], []
        def require(condition, msg):
            if not condition:failures.append(msg)
        require(not settings.DEBUG, 'Production DEBUG must be disabled')
        require(settings.SESSION_COOKIE_SECURE, 'Session cookie not TLS-only')
        require(settings.CSRF_COOKIE_SECURE, 'CSRF cookie not TLS-only')
        require(settings.SESSION_COOKIE_HTTPONLY, 'Session cookie accessible to JS')
        require(settings.SECURE_SSL_REDIRECT, 'HTTPS redirect disabled')
        require(settings.SECRET_KEY and len(settings.SECRET_KEY)>=40, 'Server secret key below policy length')
        require(settings.SECURE_REFERRER_POLICY=='same-origin', 'CSRF-safe same-origin referrer policy required')
        require(settings.BOOKING_RETENTION_DAYS == 90 and settings.FURNITURE_RETENTION_DAYS == 90,
                'Unexpected 90-day retention settings')
        require(settings.DATABASES['default']['ENGINE'].endswith(('mysql','postgresql')),
                'Production should use an isolated database engine')
        # New credentials use R14's adaptive, salted, peppered hash;
        # existing legacy hashes remain valid until normal rotation.
        pw='r15-audit-not-a-real-secret'
        encoded=make_password(pw)
        require(encoded.startswith('pbkdf2_sha256_peppered_v1$') and pw not in encoded
                and check_password(pw,encoded), 'One-way password hashing audit failed')
        require(not check_password('wrong-'+pw,encoded), 'Incorrect password accepted')
        db=settings.DATABASES['default']
        if db['ENGINE'].endswith('mysql'):
            host=db.get('HOST','')
            try:
                private=ipaddress.ip_address(host).is_private
                if not private:failures.append('Database address is not private')
            except ValueError:
                warnings.append('Database DNS target requires manual VPC verification')
            if not db.get('OPTIONS',{}).get('ssl'):
                warnings.append('MySQL CA / hostname verification is not configured')
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                    rows=cursor.fetchall()
                    cipher=rows[0][1] if rows else ''
                    if not cipher:warnings.append('MySQL client connection does not demonstrate TLS')
                    cursor.execute('SHOW GRANTS FOR CURRENT_USER')
                    grants=' '.join(str(row[0]).upper() for row in cursor.fetchall())
                if any(word in grants for word in ('ALL PRIVILEGES','GRANT OPTION',' SUPER ',' FILE ', 'CREATE USER')):
                    failures.append('Application MySQL account has potentially excessive privileges')
            except Exception:
                warnings.append('Cannot inspect MySQL transport/grant state (manual review needed)')
        # The reservation return / expiration logic is inspected by separate
        # tests; this audit does not read or print sensitive bookings.
        self.stdout.write('R15 password storage self-check completed; no secrets or records printed.')
        for msg in failures:self.stdout.write('HIGH: '+msg)
        for msg in warnings:self.stdout.write('REVIEW: '+msg)
        self.stdout.write(f'Summary: {len(failures)} high-risk finding(s), {len(warnings)} item(s) for review.')
        self.stdout.write('This audit is limited in scope; cloud firewall, encrypted backups and host access require independent review.')
        if failures:raise SystemExit(2)
