"""
Handlers for the messages that the clients may send.
"""

import struct
import time

from .constants import VNCConstants
from .regions import RegionRequest
from .events import VNCEventMove, VNCEventClick, VNCEventKey


message_handlers = {}


def register_msg(msgtype, payload_size):
    def register_func(func):
        message_handlers[msgtype] = (func, payload_size)
        return func
    return register_func


def dispatch_msg(msgtype, connection):
    """
    Dispatch a message to a handler for the client.

    We read in the payload that the message uses, and then pass this to the handler function.
    """
    if msgtype in message_handlers:
        (func, payload_size) = message_handlers[msgtype]
        name = func.__name__
        response = connection.read(payload_size, timeout=connection.payload_timeout)
        if not response:
            connection.log("Timeout reading payload data for {}".format(name))
            return False

        func(connection, response)
        return True
    else:
        connection.log("Unrecognised message type : %i" % (msgtype,))
        return False


@register_msg(VNCConstants.ClientMsgType_SetPixelFormat, payload_size=3 + 16)
def msg_SetPixelFormat(connection, payload):
    connection.pixelformat.decode(payload[3:])
    connection.log("SetPixelFormat: %r" % (connection.pixelformat,))


@register_msg(VNCConstants.ClientMsgType_SetEncodings, payload_size=1 + 2)
def msg_SetEncodings(connection, payload):
    (_, nencodings) = struct.unpack('>BH', payload)
    response = connection.read(4 * nencodings, timeout=connection.payload_timeout)
    if not response:
        connection.log("Timeout reading SetEncodings data")
        return
    encodings = struct.unpack('>' + 'l' * nencodings, response)
    connection.log("SetEncodings: %i encodings: (%r)" % (nencodings, encodings))
    encoding_names = (VNCConstants.encoding_names.get(enc, str(enc)) for enc in encodings)
    connection.log("SetEncodings: names: %s" % (', '.join(encoding_names)))
    connection.set_capabilities(encodings)


@register_msg(VNCConstants.ClientMsgType_FramebufferUpdateRequest, payload_size=1 + 2 * 4)
def msg_FramebufferUpdateRequest(connection, payload):
    (incremental, xpos, ypos, width, height) = struct.unpack('>BHHHH', payload)
    region = RegionRequest(incremental, xpos, ypos, width, height)
    #connection.log("FramebufferUpdateRequest: {!r}".format(region))
    connection.request_regions.add(region)

    # We want to track when the FrameUpdate Request comes in so that we can deliver any
    # further pushed updates after that one has been delivered and another time period
    # has passed.
    connection.last_frameupdate_request_time = time.time()
    # As soon as they request a frame, any pending frame push is discarded (because the
    # frame buffer update will cause the frame to be requested, or the next one will).
    connection.changed_frame = False


@register_msg(VNCConstants.ClientMsgType_KeyEvent, payload_size=1 + 2 + 4)
def msg_KeyEvent(connection, payload):
    (down, _, key) = struct.unpack('>BHL', payload)
    if not connection.options.read_only:
        connection.log("KeyEvent: key=%i, down=%i" % (key, down))
        connection.queue_event(VNCEventKey(key, down))


@register_msg(VNCConstants.ClientMsgType_PointerEvent, payload_size=1 + 2 * 2)
def msg_PointerEvent(connection, payload):
    (buttons, xpos, ypos) = struct.unpack('>BHH', payload)
    if not connection.options.read_only:
        connection.log("PointerEvent: buttons=%i, pos=%i,%i" % (buttons, xpos, ypos))

        # We want to be able to discard movement events and report clicks separately
        # First we deliver any movement events.
        if xpos != connection.pointer_xpos or ypos != connection.pointer_ypos:
            connection.queue_event(VNCEventMove(xpos, ypos, buttons))
            connection.pointer_xpos = xpos
            connection.pointer_ypos = ypos
        diff = connection.pointer_buttons ^ buttons
        connection.pointer_buttons = buttons
        if diff:
            # Buttons changed, so we need to deliver click or release events
            for button in range(0, 8):
                bit = (1<<button)
                if diff & bit:
                    connection.queue_event(VNCEventClick(xpos, ypos, button, buttons & bit))


@register_msg(VNCConstants.ClientMsgType_ClientCutText, payload_size=3 + 4)
def msg_ClientCutText(connection, payload):
    (_, textlen) = struct.unpack('>3sL', payload)
    response = connection.read(textlen, timeout=connection.payload_timeout)
    if not response:
        connection.log("Timeout reading ClientCutText data (2)")
        return
    if not connection.options.read_only:
        text = response.decode('iso-8859-1')
        connection.log("ClientCutText: textlen=%i, text=%r" % (textlen, text))
        # FIXME: Deliver this data
