import struct
import unittest

from cairovnc.clientmsg import msg_PointerEvent
from cairovnc.events import VNCEventClick, VNCEventMove, VNCEventScroll


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

    def log(self, message):
        pass

    def queue_event(self, event):
        self.events.append(event)


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
