"""Safeguard error reports: scrub submitted secrets and private identifiers.
The filter does not encrypt database columns; it prevents accidental disclosure
in diagnostics. Production must keep DEBUG=False and access logs scrubbed.
"""
from django.views.debug import SafeExceptionReporterFilter

PRIVATE_MARKERS = (
    'password','passwd','secret','token','credential','authorization','csrf',
    'student_id','student_name','student_contact','borrower_name','phone',
    'return_code','loan_password','student_department','db_password',
)

class RedactedExceptionFilter(SafeExceptionReporterFilter):
    """Redact unlisted private fields even without sensitive_post_parameters."""
    def get_post_parameters(self, request):
        data = super().get_post_parameters(request)
        if not data:
            return data
        copied = data.copy()
        for key in list(copied.keys()):
            if any(part in str(key).lower() for part in PRIVATE_MARKERS):
                copied[key] = self.cleansed_substitute
        return copied
