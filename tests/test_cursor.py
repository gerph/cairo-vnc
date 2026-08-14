import struct
import sys
import unittest
import zlib

from cairovnc import CairoVNCOptions, CairoVNCServer, VNCConnection, VNCCursor
from cairovnc.constants import VNCConstants
from cairovnc.errors import CairoVNCBadCursorError
from cairovnc.pixeldata import PixelFormat
from cairovnc.regions import RegionRequest


class Server(object):
    def __init__(self, cursor):
        self.cursor = cursor
        self.options = CairoVNCOptions()

    def surface_data(self):
        return (1, 1, [b'\0\0\0\0'])


class Connection(object):
    update_framebuffer = getattr(VNCConnection.update_framebuffer, 'im_func', VNCConnection.update_framebuffer)
    set_capabilities = getattr(VNCConnection.set_capabilities, 'im_func', VNCConnection.set_capabilities)
    change_cursor = getattr(VNCConnection.change_cursor, 'im_func', VNCConnection.change_cursor)


def connection(cursor):
    client = Connection()
    client.server = Server(cursor)
    client.options = client.server.options.copy()
    client.pixelformat = PixelFormat()
    client.capabilities = set()
    client.last_rows = {}
    client.changed_cursor = True
    client.zlib_compressor = zlib.compressobj()
    client.messages = []
    client.write = client.messages.append
    client.log = lambda message: None
    return client


def cursor_rectangle(message):
    message_type, padding, nrects = struct.unpack('>BBH', message[:4])
    x, y, width, height, encoding = struct.unpack('>HHHHl', message[4:16])
    return (message_type, padding, nrects, x, y, width, height, encoding,
            message[16:])


class CursorTests(unittest.TestCase):
    def test_server_keeps_surface_lock(self):
        lock = object()
        server = CairoVNCServer(None, surface_lock=lock)
        self.assertIs(lock, server.surface_lock)

    def test_validation(self):
        with self.assertRaises(CairoVNCBadCursorError):
            VNCCursor(1, 1, (1, 0), b'\0' * 4, b'\x80')
        with self.assertRaises(CairoVNCBadCursorError):
            VNCCursor(1, 1, (0, 0), b'\0' * 3, b'\x80')

    def test_alpha_mask_is_most_significant_bit_first(self):
        class Cairo(object):
            FORMAT_ARGB32 = 0
            FORMAT_RGB24 = 1
        old_cairo = sys.modules.get('cairo')
        sys.modules['cairo'] = Cairo
        try:
            class Surface(object):
                def get_width(self): return 3
                def get_height(self): return 1
                def get_format(self): return Cairo.FORMAT_ARGB32
                def get_stride(self): return 12
                def get_data(self): return b'\x01\x02\x03\xff\x04\x05\x06\0\x07\x08\x09\xff'
            cursor = VNCCursor.from_surface(Surface())
        finally:
            if old_cairo is None:
                del sys.modules['cairo']
            else:
                sys.modules['cairo'] = old_cairo
        self.assertEqual(b'\xa0', cursor.mask)
        self.assertEqual(b'\x01\x02\x03\0', cursor.pixels[:4])

    def test_cursor_is_sent_only_to_capable_clients(self):
        cursor = VNCCursor(2, 1, (1, 0), b'\x01\x02\x03\0\x04\x05\x06\0', b'\xc0')
        client = connection(cursor)
        client.last_rows[0] = b'\0\0\0\0'
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 1))
        self.assertEqual(struct.pack('>BBH', 0, 0, 0), client.messages[0])
        client.changed_cursor = True
        client.set_capabilities([VNCConstants.PseudoEncoding_Cursor])
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 1))
        (_, _, nrects, x, y, width, height, encoding, payload) = cursor_rectangle(client.messages[1])
        self.assertEqual(1, nrects)
        self.assertEqual(cursor.hotspot + (cursor.width, cursor.height), (x, y, width, height))
        self.assertEqual(VNCConstants.PseudoEncoding_Cursor, encoding)
        self.assertEqual(cursor.pixels + cursor.mask, payload)
        self.assertFalse(client.changed_cursor)

    def test_changed_cursor_is_delivered(self):
        cursor = VNCCursor(2, 1, (1, 0), b'\x01\x02\x03\0\x04\x05\x06\0', b'\xc0')
        client = connection(cursor)
        client.last_rows[0] = b'\0\0\0\0'
        client.set_capabilities([VNCConstants.PseudoEncoding_Cursor])
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 1))
        client.change_cursor()
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 1))
        self.assertEqual(2, len(client.messages))
        (_, _, nrects, x, y, width, height, encoding, payload) = cursor_rectangle(client.messages[1])
        self.assertEqual(1, nrects)
        self.assertEqual(cursor.hotspot + (cursor.width, cursor.height), (x, y, width, height))
        self.assertEqual(VNCConstants.PseudoEncoding_Cursor, encoding)
        self.assertEqual(cursor.pixels + cursor.mask, payload)
        self.assertFalse(client.changed_cursor)

    def test_cursor_pixels_use_client_pixel_format(self):
        cursor = VNCCursor(1, 1, (0, 0), b'\x11\x22\x33\0', b'\x80')
        client = connection(cursor)
        client.last_rows[0] = b'\0\0\0\0'
        client.pixelformat.bpp = 16
        client.pixelformat.depth = 16
        client.pixelformat.redmax = 31
        client.pixelformat.greenmax = 63
        client.pixelformat.bluemax = 31
        client.pixelformat.redshift = 11
        client.pixelformat.greenshift = 5
        client.pixelformat.blueshift = 0
        client.set_capabilities([VNCConstants.PseudoEncoding_Cursor])
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 1))
        self.assertEqual(b'\x02\x31', client.messages[0][16:18])
