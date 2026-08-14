import struct
import unittest

from cairovnc import CairoVNCOptions, CairoVNCServer, VNCConnection
from cairovnc.constants import VNCConstants


class Connection(object):
    send_clipboard = getattr(VNCConnection.send_clipboard, 'im_func', VNCConnection.send_clipboard)
    change_clipboard = getattr(VNCConnection.change_clipboard, 'im_func', VNCConnection.change_clipboard)
    setup = getattr(VNCConnection.setup, 'im_func', VNCConnection.setup)

    def __init__(self, text):
        self.server = type('Server', (object,), {'clipboard': text})()
        self.messages = []
        self.write = self.messages.append
        self.log = lambda message: None
        self.changed_clipboard = False


class ClipboardTests(unittest.TestCase):
    def test_server_cut_text_framing(self):
        connection = Connection(u'hello\xa3')
        connection.send_clipboard()
        expected = struct.pack('>B3sL', VNCConstants.ServerMsgType_ServerCutText,
                               b'\0\0\0', 6) + b'hello\xa3'
        self.assertEqual([expected], connection.messages)

    def test_change_clipboard_marks_connection_pending(self):
        connection = Connection(None)
        connection.change_clipboard()
        self.assertTrue(connection.changed_clipboard)

    def test_initial_clipboard_is_pending_after_setup(self):
        connection = Connection(u'initial')
        connection.server.options = CairoVNCOptions()
        connection.request = object()
        connection.setup()
        self.assertTrue(connection.changed_clipboard)

    def test_public_server_changes_clipboard_before_start(self):
        server = CairoVNCServer(None)
        server.change_clipboard(u'hello')
        self.assertEqual(u'hello', server.clipboard)

    def test_clipboard_must_be_latin_one_text(self):
        server = CairoVNCServer(None)
        with self.assertRaises(UnicodeEncodeError):
            server.change_clipboard(u'\u20ac')
