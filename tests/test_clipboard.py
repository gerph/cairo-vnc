import struct
import unittest
import zlib

from cairovnc import CairoVNCOptions, CairoVNCServer, VNCConnection, VNCServer
from cairovnc.clipboard import VNCClipboard
from cairovnc.constants import VNCConstants


class Connection(object):
    send_clipboard = getattr(VNCConnection.send_clipboard, 'im_func', VNCConnection.send_clipboard)
    change_clipboard = getattr(VNCConnection.change_clipboard, 'im_func', VNCConnection.change_clipboard)
    setup = getattr(VNCConnection.setup, 'im_func', VNCConnection.setup)
    set_capabilities = getattr(VNCConnection.set_capabilities, 'im_func', VNCConnection.set_capabilities)
    send_extended_clipboard_capabilities = getattr(VNCConnection.send_extended_clipboard_capabilities, 'im_func', VNCConnection.send_extended_clipboard_capabilities)
    send_extended_clipboard_message = getattr(VNCConnection.send_extended_clipboard_message, 'im_func', VNCConnection.send_extended_clipboard_message)
    send_extended_clipboard_action = getattr(VNCConnection.send_extended_clipboard_action, 'im_func', VNCConnection.send_extended_clipboard_action)
    send_extended_clipboard_provide = getattr(VNCConnection.send_extended_clipboard_provide, 'im_func', VNCConnection.send_extended_clipboard_provide)
    receive_extended_clipboard = getattr(VNCConnection.receive_extended_clipboard, 'im_func', VNCConnection.receive_extended_clipboard)
    receive_extended_clipboard_capabilities = getattr(VNCConnection.receive_extended_clipboard_capabilities, 'im_func', VNCConnection.receive_extended_clipboard_capabilities)
    receive_extended_clipboard_provide = getattr(VNCConnection.receive_extended_clipboard_provide, 'im_func', VNCConnection.receive_extended_clipboard_provide)
    decompress_extended_clipboard = getattr(VNCConnection.decompress_extended_clipboard, 'im_func', VNCConnection.decompress_extended_clipboard)
    extended_clipboard_formats = getattr(VNCConnection.extended_clipboard_formats, 'im_func', VNCConnection.extended_clipboard_formats)

    def __init__(self, text):
        self.server = type('Server', (object,), {
            'clipboard': VNCClipboard({VNCClipboard.Format_Text: text}) if text is not None else None,
            'options': CairoVNCOptions()
        })()
        self.options = self.server.options.copy()
        self.options.read_only = False
        self.capabilities = set()
        self.messages = []
        self.events = []
        self.write = self.messages.append
        self.log = lambda message: None
        self.queue_event = self.events.append
        self.changed_clipboard = False
        self.extended_clipboard_capabilities = None
        self.extended_clipboard_limits = {}


class ClipboardTests(unittest.TestCase):
    def test_vnc_server_normalises_clipboard(self):
        clipboard = VNCClipboard({VNCClipboard.Format_Text: u'hello'})
        server = VNCServer(('127.0.0.1', 0), object,
                           options=CairoVNCOptions(), clipboard=clipboard)
        try:
            self.assertIs(clipboard, server.clipboard)
        finally:
            server.server_close()

    def test_server_cut_text_framing(self):
        connection = Connection(u'hello\xa3')
        connection.send_clipboard()
        expected = struct.pack('>B3sL', VNCConstants.ServerMsgType_ServerCutText,
                               b'\0\0\0', 6) + b'hello\xa3'
        self.assertEqual([expected], connection.messages)

    def test_extended_capabilities_are_sent_after_negotiation(self):
        connection = Connection(u'hello')
        connection.set_capabilities([VNCConstants.PseudoEncoding_ExtendedClipboard])
        message = connection.messages[0]
        _, _, length = struct.unpack('>B3sl', message[:8])
        self.assertLess(length, 0)
        flags, = struct.unpack('>L', message[8:12])
        self.assertTrue(flags & VNCConstants.Clipboard_Action_Caps)
        self.assertEqual(VNCClipboard.Format_Text | VNCClipboard.Format_RTF | VNCClipboard.Format_HTML,
                         flags & 0xffff)
        self.assertEqual(b'\0' * 12, zlib.decompress(message[12:]))

    def test_extended_provide_contains_utf8_text_rtf_and_html(self):
        connection = Connection(u'hello\n\xa3')
        connection.server.clipboard.set(VNCClipboard.Format_RTF, b'{\\rtf1}')
        connection.server.clipboard.set(VNCClipboard.Format_HTML, b'<b>hello</b>')
        formats = connection.server.clipboard.format_flags()
        connection.send_extended_clipboard_provide(formats)
        message = connection.messages[0]
        flags, = struct.unpack('>L', message[8:12])
        self.assertEqual(formats | VNCConstants.Clipboard_Action_Provide, flags)
        payload = zlib.decompress(message[12:])
        values = []
        offset = 0
        for unused in range(3):
            size, = struct.unpack('>L', payload[offset:offset + 4])
            offset += 4
            values.append(payload[offset:offset + size])
            offset += size
        self.assertEqual([u'hello\r\n\xa3'.encode('utf-8') + b'\0',
                          b'{\\rtf1}', b'<b>hello</b>'], values)

    def test_extended_provide_is_queued_with_supported_formats(self):
        connection = Connection(None)
        formats = VNCClipboard.Format_Text | VNCClipboard.Format_RTF | VNCClipboard.Format_HTML
        data = (struct.pack('>L', 8) + u'hello\xa3'.encode('utf-8') + b'\0' +
                struct.pack('>L', 7) + b'{\\rtf1}' +
                struct.pack('>L', 8) + b'<b>x</b>')
        connection.capabilities.add(VNCConstants.PseudoEncoding_ExtendedClipboard)
        connection.receive_extended_clipboard(struct.pack('>L', formats |
                                                           VNCConstants.Clipboard_Action_Provide) +
                                              zlib.compress(data))
        clipboard = connection.events[0].clipboard
        self.assertEqual(u'hello\xa3', clipboard.text)
        self.assertEqual(b'{\\rtf1}', clipboard.formats[VNCClipboard.Format_RTF])
        self.assertEqual(b'<b>x</b>', clipboard.formats[VNCClipboard.Format_HTML])

    def test_extended_notify_requests_supported_formats(self):
        connection = Connection(None)
        connection.capabilities.add(VNCConstants.PseudoEncoding_ExtendedClipboard)
        connection.receive_extended_clipboard(struct.pack(
            '>L', VNCClipboard.Format_Text | VNCClipboard.Format_RTF |
            VNCConstants.Clipboard_Action_Notify))
        flags, = struct.unpack('>L', connection.messages[0][8:12])
        self.assertEqual(VNCClipboard.Format_Text | VNCClipboard.Format_RTF |
                         VNCConstants.Clipboard_Action_Request, flags)

    def test_extended_request_receives_provide(self):
        connection = Connection(u'hello')
        connection.capabilities.add(VNCConstants.PseudoEncoding_ExtendedClipboard)
        connection.receive_extended_clipboard(struct.pack(
            '>L', VNCClipboard.Format_Text | VNCConstants.Clipboard_Action_Request))
        flags, = struct.unpack('>L', connection.messages[0][8:12])
        self.assertEqual(VNCClipboard.Format_Text |
                         VNCConstants.Clipboard_Action_Provide, flags)

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
        self.assertEqual(u'hello', server.clipboard.text)

    def test_legacy_client_does_not_receive_unrepresentable_text(self):
        connection = Connection(u'\u20ac')
        connection.send_clipboard()
        self.assertEqual([], connection.messages)

    def test_extended_clipboard_is_ignored_without_negotiation(self):
        connection = Connection(None)
        logs = []
        connection.log = logs.append
        connection.receive_extended_clipboard(struct.pack(
            '>L', VNCClipboard.Format_Text | VNCConstants.Clipboard_Action_Notify))
        self.assertEqual([], connection.events)
        self.assertTrue(logs)

    def test_extended_clipboard_ignores_unknown_formats(self):
        connection = Connection(None)
        connection.capabilities.add(VNCConstants.PseudoEncoding_ExtendedClipboard)
        unknown = 1 << 3
        text = b'hello\0'
        data = (struct.pack('>L', len(text)) + text +
                struct.pack('>L', 3) + b'xyz')
        connection.receive_extended_clipboard(struct.pack(
            '>L', unknown | VNCClipboard.Format_Text |
            VNCConstants.Clipboard_Action_Provide) + zlib.compress(data))
        self.assertEqual(u'hello', connection.events[0].clipboard.text)
        self.assertNotIn(unknown, connection.events[0].clipboard.formats)

    def test_invalid_extended_compression_is_rejected(self):
        connection = Connection(None)
        self.assertIsNone(connection.decompress_extended_clipboard(b'not zlib', 1024))
        self.assertIsNone(connection.decompress_extended_clipboard(
            zlib.compress(b'a' * 16), 8))
        self.assertIsNone(connection.decompress_extended_clipboard(
            zlib.compress(b'ok') + b'trailing', 1024))

    def test_malformed_extended_provide_is_rejected(self):
        connection = Connection(None)
        connection.capabilities.add(VNCConstants.PseudoEncoding_ExtendedClipboard)
        flags = VNCClipboard.Format_Text | VNCConstants.Clipboard_Action_Provide
        connection.receive_extended_clipboard(struct.pack('>L', flags) +
                                              zlib.compress(struct.pack('>L', 8) + b'x'))
        self.assertEqual([], connection.events)
        connection.options.clipboard_maximum_size = 4
        connection.receive_extended_clipboard(struct.pack('>L', flags) +
                                              zlib.compress(struct.pack('>L', 5) + b'hello'))
        self.assertEqual([], connection.events)

    def test_read_only_extended_provide_is_not_queued(self):
        connection = Connection(None)
        connection.options.read_only = True
        connection.capabilities.add(VNCConstants.PseudoEncoding_ExtendedClipboard)
        data = struct.pack('>L', 3) + b'ok\0'
        connection.receive_extended_clipboard(struct.pack(
            '>L', VNCClipboard.Format_Text | VNCConstants.Clipboard_Action_Provide) +
                                              zlib.compress(data))
        self.assertEqual([], connection.events)
