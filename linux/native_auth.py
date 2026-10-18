"""Exchange an origin-bound device credential for a short-lived gateway cookie."""
from http.cookies import SimpleCookie
from pathlib import Path
import json
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.error import HTTPError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_credentials(path, origin):
    path = Path(path)
    if not path.exists():
        return None
    if path.stat().st_mode & 0o077:
        raise ValueError('Device credential permissions must be 0600.')
    value = json.loads(path.read_text())
    if value.get('origin') != origin:
        return None
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Device credentials require an exact HTTPS origin.')
    if not all(isinstance(value.get(k), str) and value[k] for k in ('username', 'password')):
        raise ValueError('Incomplete device credentials.')
    return value


def session_cookie(credentials):
    origin = credentials['origin']
    data = urlencode({'username': credentials['username'], 'password': credentials['password'],
                      'rd': '/'}).encode()
    request = Request(origin + '/oauth2/sign_in', data=data,
                      headers={'Content-Type': 'application/x-www-form-urlencoded', 'Origin': origin})
    try:
        response = build_opener(NoRedirect()).open(request, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        if response.status != 302 or response.headers.get('Location') not in ('/', origin + '/'):
            raise ValueError('Device sign-in failed; check the registered device credential.')
        cookies = SimpleCookie()
        for header in response.headers.get_all('Set-Cookie', []):
            cookies.load(header)
        cookie = cookies.get('__Host-energy')
        if (not cookie or not cookie.value or not cookie['secure'] or not cookie['httponly']
                or cookie['path'] != '/' or cookie['domain']):
            raise ValueError('Gateway did not issue a secure device session.')
        return cookie.value
