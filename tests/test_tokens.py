"""
JWT fuer Passwort-Reset- und Bestaetigungslinks (joserfc).
Braucht keine Datenbank: der User wird nur im Speicher angelegt.
"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from bson import ObjectId

from application import app
from application.auth.views import read_reset_token
from application.models.user import User

# Mit dem frueheren authlib.jose-Code erzeugt (Authlib 1.9.4, jwt.encode wie in
# User.generate_token), Schluessel OLD_KEY. Links aus Mails vor dem Umstieg
# muessen weiter gelten.
OLD_KEY = 'alter-schluessel-fuer-tests'
OLD_CLAIMS = {
    'userid': '65f0c0ffee0000000000abcd',
    'pwd': '0123456789abcdef0123456789abcdef',
    'iat': 1794312000,
}
OLD_TOKEN_VALID = (  # exp 4102444800.25 (Jahr 2100)
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyaWQiOiI2NWYwYzBmZmVlMDAwMDAwMDAwMGFiY2Qi'
    'LCJwd2QiOiIwMTIzNDU2Nzg5YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZiIsImV4cCI6NDEwMjQ0NDgwMC4yNSwi'
    'aWF0IjoxNzk0MzEyMDAwfQ.ZYct7TwkQddfoJvleJP56XtWrf-AbFwskSxYZCbdJeA'
)
OLD_TOKEN_EXPIRED = (  # exp 1767225600.5 (Anfang 2026)
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyaWQiOiI2NWYwYzBmZmVlMDAwMDAwMDAwMGFiY2Qi'
    'LCJwd2QiOiIwMTIzNDU2Nzg5YWJjZGVmMDEyMzQ1Njc4OWFiY2RlZiIsImV4cCI6MTc2NzIyNTYwMC41LCJp'
    'YXQiOjE3OTQzMTIwMDB9.i1dn3P2bxw3_AeV8hq29ZWrEj4BRs7ucqmLkCgTa2Ck'
)


class TokenTest(unittest.TestCase):
    """generate_token und read_reset_token"""

    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.user = User(id=ObjectId(), email='user@example.org', pwdhash='hash')

    def test_round_trip(self):
        claims = read_reset_token(self.user.generate_token())
        self.assertEqual(claims['userid'], str(self.user.id))
        self.assertEqual(claims['pwd'], self.user.password_fingerprint())
        self.assertIsInstance(claims['iat'], int)
        self.assertAlmostEqual(claims['exp'], (datetime.utcnow() + timedelta(seconds=3600)).timestamp(),
                               delta=5)

    def test_expired_token_rejected(self):
        token = self.user.generate_token(expiration=60)
        later = datetime.utcnow() + timedelta(seconds=61)
        with mock.patch('application.auth.views.datetime') as fake:
            fake.utcnow.return_value = later
            with self.assertRaises(ValueError):
                read_reset_token(token)

    def test_tampered_token_rejected(self):
        token = self.user.generate_token()
        header, payload, signature = token.split('.')
        other = User(id=ObjectId(), pwdhash='hash').generate_token().split('.')[1]
        bad_signature = ('A' if signature[0] != 'A' else 'B') + signature[1:]
        for bad in (f'{header}.{other}.{signature}', f'{header}.{payload}.{bad_signature}',
                    'kein-token', ''):
            with self.subTest(token=bad), self.assertRaises(ValueError):
                read_reset_token(bad)

    def test_wrong_key_rejected(self):
        token = self.user.generate_token()
        with mock.patch.dict(app.config, SECRET_KEY='ein-ganz-anderer-schluessel'):
            with self.assertRaises(ValueError):
                read_reset_token(token)

    def test_unsigned_token_rejected(self):
        # alg "none" darf nie durchgehen
        token = 'eyJhbGciOiJub25lIn0.' + self.user.generate_token().split('.')[1] + '.'
        with self.assertRaises(ValueError):
            read_reset_token(token)

    def test_old_authlib_token_still_valid(self):
        with mock.patch.dict(app.config, SECRET_KEY=OLD_KEY):
            claims = read_reset_token(OLD_TOKEN_VALID)
            self.assertEqual({k: claims[k] for k in OLD_CLAIMS}, OLD_CLAIMS)
            self.assertEqual(claims['exp'], 4102444800.25)
            with self.assertRaises(ValueError):
                read_reset_token(OLD_TOKEN_EXPIRED)


if __name__ == '__main__':
    unittest.main()
