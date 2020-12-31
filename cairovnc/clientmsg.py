"""
Handlers for the messages that the clients may send.
"""

import struct

from .constants import VNCConstants
from .regions import RegionRequest
from .events import VNCEventMove, VNCEventClick, VNCEventKey


message_handlers = {}


def register_msg(msgtype, payload_size):
    def register_func(func):
        message_handlers[msgtype] = (func, payload_size)
        return func
    return register_func


def dispatch_msg(msgtype, server):
    """
    Dispatch a message to a handler for the client.

    We read in the payload that the message uses, and then pass this to the handler function.
    """
    if msgtype in message_handlers:
        (func, payload_size) = message_handlers[msgtype]
        name = func.__name__
        response = server.read(payload_size, timeout=server.payload_timeout)
        if not response:
            server.log("Timeout reading payload data for {}".format(name))
            return False

        func(server, response)
        return True
    else:
        server.log("Unrecognised message type : %i" % (msgtype,))
        return False


@register_msg(VNCConstants.ClientMsgType_SetPixelFormat, payload_size=3 + 16)
def msg_SetPixelFormat(server, payload):
    server.pixelformat.decode(payload[3:])
    server.log("SetPixelFormat: %r" % (server.pixelformat,))


@register_msg(VNCConstants.ClientMsgType_SetEncodings, payload_size=1 + 2)
def msg_SetEncodings(server, payload):
    (_, nencodings) = struct.unpack('>BH', payload)
    response = server.read(4 * nencodings, timeout=server.payload_timeout)
    if not response:
        server.log("Timeout reading SetEncodings data")
        return
    encodings = struct.unpack('>' + 'l' * nencodings, response)
    server.log("SetEncodings: %i encodings: (%r)" % (nencodings, encodings))
    encoding_names = (VNCConstants.encoding_names.get(enc, str(enc)) for enc in encodings)
    server.log("SetEncodings: names: %s" % (', '.join(encoding_names)))
    server.capabilities = set(encodings)


@register_msg(VNCConstants.ClientMsgType_FramebufferUpdateRequest, payload_size=1 + 2 * 4)
def msg_FramebufferUpdateRequest(server, payload):
    (incremental, xpos, ypos, width, height) = struct.unpack('>BHHHH', payload)
    region = RegionRequest(incremental, xpos, ypos, width, height)
    #server.log("FramebufferUpdateRequest: {!r}".format(region))
    server.request_regions.add(region)


@register_msg(VNCConstants.ClientMsgType_KeyEvent, payload_size=1 + 2 + 4)
def msg_KeyEvent(server, payload):
    (down, _, key) = struct.unpack('>BHL', payload)
    if not server.options.read_only:
        server.log("KeyEvent: key=%i, down=%i" % (key, down))
        server.queue_event(VNCEventKey(key, down))


@register_msg(VNCConstants.ClientMsgType_PointerEvent, payload_size=1 + 2 * 2)
def msg_PointerEvent(server, payload):
    (buttons, xpos, ypos) = struct.unpack('>BHH', payload)
    if not server.options.read_only:
        server.log("PointerEvent: buttons=%i, pos=%i,%i" % (buttons, xpos, ypos))

        # We want to be able to discard movement events and report clicks separately
        # First we deliver any movement events.
        if xpos != server.pointer_xpos or ypos != server.pointer_ypos:
            server.queue_event(VNCEventMove(xpos, ypos, buttons))
        diff = server.pointer_buttons ^ buttons
        if diff:
            # Buttons changed, so we need to deliver click or release events
            for button in range(0, 8):
                bit = (1<<button)
                if diff & bit:
                    server.queue_event(VNCEventClick(xpos, ypos, button, buttons & bit))


@register_msg(VNCConstants.ClientMsgType_ClientCutText, payload_size=3 + 4)
def msg_ClientCutText(server, payload):
    (_, textlen) = struct.unpack('>3sL', payload)
    response = server.read(textlen, timeout=server.payload_timeout)
    if not response:
        server.log("Timeout reading ClientCutText data (2)")
        return
    if not server.options.read_only:
        text = response.decode('iso-8859-1')
        server.log("ClientCutText: textlen=%i, text=%r" % (textlen, text))
        # FIXME: Deliver this data
