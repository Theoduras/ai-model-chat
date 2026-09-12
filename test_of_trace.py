import logging
import unittest

import of_trace


class TraceTest(unittest.TestCase):

    def setUp(self):
        of_trace.clear()
        of_trace.install()

    def test_a_plumbing_log_line_reaches_the_console(self):
        logging.getLogger('of_client').warning('signature refused: %s', 401)
        line = of_trace.recent()[-1]
        self.assertEqual(line['where'], 'client')
        self.assertEqual(line['level'], 'warning')
        self.assertIn('401', line['text'])

    def test_only_lines_after_the_cursor_come_back(self):
        of_trace.note('rules', 'one')
        first = of_trace.recent()[-1]['id']
        of_trace.note('rules', 'two')
        self.assertEqual([r['text'] for r in of_trace.recent(after=first)], ['two'])

    def test_installing_twice_does_not_double_every_line(self):
        of_trace.install()
        logging.getLogger('of_events').info('polled')
        self.assertEqual(len(of_trace.recent()), 1)

    def test_the_ring_forgets_rather_than_growing(self):
        for i in range(of_trace.MAX + 40):
            of_trace.note('rules', str(i))
        self.assertEqual(len(of_trace.recent(limit=of_trace.MAX + 40)), of_trace.MAX)


if __name__ == '__main__':
    unittest.main()
