"""
Objects used for passing events to the animator system.

Key events:
    Report key codes for the press and release events.
    Multiple repeating 'press' events may occur whilst the key is pressed for auto-repeat.

Mouse events:
    Separated into movement events and click events.
    Movement events will always be delivered first, followed by any click event.
    Click events describe the transition of the mouse buttons in the same manner as the key events,
    although without auto-repeat.
"""

class VNCEvent(object):
    """
    Base class for events.
    """
    # Name, which should be overridden (may be used to identify the event type without using the class)
    name = 'base'

    def __init__(self):
        self.timestamp = time.time()

    def __repr__(self):
        return "<{}()>".format(self.__class__.__name__)


class VNCEventKey(object):
    """
    A Key input event (press or release).
    """
    name = 'key'

    # Some constants for common keys
    Key_Backspace       = 0xff08
    Key_Tab             = 0xff09
    Key_Return          = 0xff0d
    Key_Escape          = 0xff1b
    Key_Insert          = 0xff63
    Key_Delete          = 0xffff
    Key_Home            = 0xff50
    Key_End             = 0xff57
    Key_PageUp          = 0xff55
    Key_PageDown        = 0xff56
    Key_CursorLeft      = 0xff51
    Key_CursorUp        = 0xff52
    Key_CursorRight     = 0xff53
    Key_CursorDown      = 0xff54
    Key_F1              = 0xffbe
    Key_F2              = 0xffbf
    Key_F3              = 0xffc0
    Key_F4              = 0xffc1
    Key_F5              = 0xffc2
    Key_F6              = 0xffc3
    Key_F7              = 0xffc4
    Key_F8              = 0xffc5
    Key_F9              = 0xffc6
    Key_F10             = 0xffc7
    Key_F11             = 0xffc8
    Key_F12             = 0xffc9
    Key_ShiftLeft       = 0xffe1
    Key_ShiftRight      = 0xffe2
    Key_ControlLeft     = 0xffe3
    Key_ControlRight    = 0xffe4
    Key_MetaLeft        = 0xffe7
    Key_MetaRight       = 0xffe8
    Key_AltLeft         = 0xffe9
    Key_AltRight        = 0xffea

    def __init__(self, key, down):
        """
        A key press or release event.
        """
        super(VNCEventKey, self).__init__()
        self.key = key
        self.down = bool(down)


class VNCEventMove(object):
    """
    A pointer move event.
    """
    name = 'move'

    def __init__(self, x, y, buttons):
        super(VNCEventMove, self).__init__()
        self.x = x
        self.y = y
        self.buttons = buttons


class VNCEventClick(object):
    """
    A pointer click event (press or release).
    """
    name = 'click'

    def __init__(self, x, y, button, down):
        super(VNCEventClick, self).__init__()
        self.x = x
        self.y = y
        self.button = button
        self.down = bool(down)
