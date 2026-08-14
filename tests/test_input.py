import struct
import unittest

from cairovnc.clientmsg import msg_ClientCutText, msg_PointerEvent
from cairovnc.events import VNCEventClick, VNCEventClipboard, VNCEventMove, VNCEventScroll


class Options(object):
    read_only = False


class Connection(object):
    def __init__(self, read_only=False):
        self.options = Options()
        self.options.read_only = read_only
        self.pointer_buttons = 0
        self.pointer_wire_buttons = 0
        self.pointer_xpos = -1
        self.pointer_ypos = -1
        self.events = []
        self.payload_timeout = 1
        self.clipboard_data = None

    def log(self, message):
        pass

    def queue_event(self, event):
        self.events.append(event)

    def read(self, size, timeout):
        return self.clipboard_data


def pointer(buttons, x=10, y=20):
    return struct.pack('>BHH', buttons, x, y)


class PointerEventTests(unittest.TestCase):
    def test_scroll_press_and_release(self):
        connection = Connection()
        msg_PointerEvent(connection, pointer(0x08))
        msg_PointerEvent(connection, pointer(0))
        self.assertEqual(2, len(connection.events))
        self.assertIsInstance(connection.events[0], VNCEventMove)
        self.assertIsInstance(connection.events[1], VNCEventScroll)
        self.assertEqual((0, 1), (connection.events[1].dx, connection.events[1].dy))

    def test_repeated_and_combined_scroll_steps(self):
        connection = Connection()
        msg_PointerEvent(connection, pointer(0x08 | 0x40))
        msg_PointerEvent(connection, pointer(0))
        msg_PointerEvent(connection, pointer(0x10))
        msg_PointerEvent(connection, pointer(0))
        msg_PointerEvent(connection, pointer(0x08))
        steps = [(event.dx, event.dy) for event in connection.events
                 if isinstance(event, VNCEventScroll)]
        self.assertEqual([(0, 1), (1, 0), (0, -1), (0, 1)], steps)

    def test_movement_uses_no_wheel_button_bits(self):
        connection = Connection()
        msg_PointerEvent(connection, pointer(0x08, 4, 5))
        self.assertEqual(0, connection.events[0].buttons)

    def test_read_only_suppresses_scroll(self):
        connection = Connection(True)
        msg_PointerEvent(connection, pointer(0x08))
        self.assertEqual([], connection.events)

    def test_button_eight_remains_a_click(self):
        connection = Connection()
        msg_PointerEvent(connection, pointer(0x80))
        msg_PointerEvent(connection, pointer(0))
        clicks = [event for event in connection.events if isinstance(event, VNCEventClick)]
        self.assertEqual([(7, True), (7, False)],
                         [(event.button, event.down) for event in clicks])

    def test_normal_click_after_scroll_is_unchanged(self):
        connection = Connection()
        msg_PointerEvent(connection, pointer(0x08))
        msg_PointerEvent(connection, pointer(0))
        msg_PointerEvent(connection, pointer(0x40))
        msg_PointerEvent(connection, pointer(0))
        msg_PointerEvent(connection, pointer(0x01))
        msg_PointerEvent(connection, pointer(0))
        clicks = [event for event in connection.events if isinstance(event, VNCEventClick)]
        self.assertEqual([(0, True), (0, False)],
                         [(event.button, event.down) for event in clicks])
        self.assertEqual(0, connection.pointer_buttons)

    def test_client_clipboard_is_queued_as_text(self):
        connection = Connection()
        connection.clipboard_data = b'hello\xa3'
        msg_ClientCutText(connection, struct.pack('>3sL', b'\0\0\0',
                                                   len(connection.clipboard_data)))
        self.assertEqual(1, len(connection.events))
        self.assertIsInstance(connection.events[0], VNCEventClipboard)
        self.assertEqual(u'hello\xa3', connection.events[0].text)

    def test_read_only_suppresses_client_clipboard(self):
        connection = Connection(True)
        connection.clipboard_data = b'private'
        msg_ClientCutText(connection, struct.pack('>3sL', b'\0\0\0',
                                                   len(connection.clipboard_data)))
        self.assertEqual([], connection.events)
