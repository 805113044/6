class PrivacyHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ("TRACE", "TRACK"):
            from django.http import HttpResponseNotAllowed
            response = HttpResponseNotAllowed(["GET", "HEAD", "POST"])
        else:
            response = self.get_response(request)
        if not request.path.startswith("/static/"):
            response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "same-origin"
        response["X-Permitted-Cross-Domain-Policies"] = "none"
        response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response["Cross-Origin-Resource-Policy"] = "same-origin"
        if not request.path.startswith("/static/"):
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"
        response["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response
