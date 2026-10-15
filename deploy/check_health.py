import os
from urllib.request import Request, urlopen

host = os.environ["ALLOWED_HOSTS"].split(",")[0].strip()
request = Request(
    "http://127.0.0.1:8000/healthz/",
    headers={"Host": host, "X-Forwarded-Proto": "https"},
)
with urlopen(request, timeout=4) as response:
    assert response.status == 200
    assert b'"ok"' in response.read()
