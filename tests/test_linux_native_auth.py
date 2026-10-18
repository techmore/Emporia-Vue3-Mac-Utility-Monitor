import importlib.util
import json
from email.message import Message
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('native_auth', Path(__file__).parents[1]/'linux/native_auth.py')
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class NativeAuthTests(unittest.TestCase):
    def test_origin_binding_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'device.json'
            value = {'origin':'https://energy.example', 'username':'device', 'password':'private'}
            path.write_text(json.dumps(value))
            path.chmod(0o600)
            self.assertEqual(auth.load_credentials(path, value['origin']), value)
            self.assertIsNone(auth.load_credentials(path, 'https://other.example'))
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                auth.load_credentials(path, value['origin'])
            path.chmod(0o600)
            value['origin'] = 'http://energy.example'
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                auth.load_credentials(path, value['origin'])

    def response(self, cookie, location='/', status=302):
        response = MagicMock()
        response.status = status
        response.headers = Message()
        response.headers['Location'] = location
        response.headers['Set-Cookie'] = cookie
        response.__enter__.return_value = response
        return response

    def test_secure_cookie_and_password_sent_only_in_body(self):
        response = self.response('__Host-energy=session; Path=/; Secure; HttpOnly')
        with patch.object(auth, 'build_opener') as build:
            build.return_value.open.return_value = response
            value = auth.session_cookie({'origin':'https://energy.example', 'username':'device', 'password':'private'})
            self.assertEqual(value, 'session')
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://energy.example/oauth2/sign_in')
            self.assertIn(b'password=private', request.data)
            self.assertNotIn('private', request.full_url)
            self.assertIsInstance(build.call_args.args[0], auth.NoRedirect)

    def test_invalid_login_and_cookie_flags_rejected(self):
        for cookie, location, status in (
                ('__Host-energy=x; Path=/; Secure', '/', 302),
                ('__Host-energy=x; Path=/; Secure; HttpOnly; Domain=energy.example', '/', 302),
                ('__Host-energy=x; Path=/; Secure; HttpOnly', 'https://accounts.google.com/', 302),
                ('', '/', 401)):
            with self.subTest(cookie=cookie, status=status), patch.object(auth, 'build_opener') as build:
                build.return_value.open.return_value = self.response(cookie, location, status)
                with self.assertRaises(ValueError):
                    auth.session_cookie({'origin':'https://energy.example', 'username':'device', 'password':'private'})
        self.assertIsNone(auth.NoRedirect().redirect_request(None, None, 307, '', {}, 'https://other.example'))
