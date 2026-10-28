"""
Tests fuer die Filter der Event-Liste.
Ausfuehren: .venv/bin/python -m unittest discover tests
Kein Mongo/Redis noetig, DB-Zugriffe sind gemockt.
"""
import unittest
from unittest import mock

from application import app
from application.events import views


class EventListFilterTest(unittest.TestCase):

    def list_events(self, url):
        """Ruft page_list per GET auf und liefert die Argumente von Event.objects"""
        user = mock.MagicMock(is_authenticated=False)
        with app.test_request_context(url), \
             mock.patch.object(views, 'current_user', user), \
             mock.patch.object(views, 'get_categories', return_value={}), \
             mock.patch.object(views, 'render_template'), \
             mock.patch.object(views.Event, 'objects') as objects:
            views.page_list()
        return objects.call_args

    def test_first_visit_shows_only_future(self):
        args, _ = self.list_events('/')
        self.assertTrue(args, "Ohne Filter gilt 'Zukünftige'")

    def test_submitted_without_future_shows_past(self):
        """Nur 'Zukünftige' abgewaehlt: auch vergangene Touren anzeigen"""
        args, _ = self.list_events('/?search=1')
        self.assertFalse(args)

    def test_submitted_with_future(self):
        args, _ = self.list_events('/?search=1&filter_future=y')
        self.assertTrue(args)

    def test_post_redirect_marks_search(self):
        app.config['WTF_CSRF_ENABLED'] = False
        try:
            with app.test_request_context('/', method='POST', data={'filter_category': 'None', 'filter_send': 'Filter'}), \
                 mock.patch.object(views, 'get_categories', return_value={}):
                response = views.page_list()
        finally:
            app.config['WTF_CSRF_ENABLED'] = True
        self.assertIn('search=1', response.location)
        self.assertNotIn('filter_future', response.location)


if __name__ == '__main__':
    unittest.main()
