import struct
import unittest
import zlib

from cairovnc import CairoVNCOptions, VNCConnection
from cairovnc.constants import VNCConstants
from cairovnc.pixeldata import PixelFormat
from cairovnc.regions import RegionRequest, Regions


class Server(object):
    def __init__(self, rows):
        self.rows = rows
        self.options = CairoVNCOptions()

    def surface_data(self):
        return (len(self.rows[0]) // 4, len(self.rows), self.rows)


class Connection(object):
    update_framebuffer = getattr(VNCConnection.update_framebuffer, 'im_func', VNCConnection.update_framebuffer)
    set_capabilities = getattr(VNCConnection.set_capabilities, 'im_func', VNCConnection.set_capabilities)
    queue_push_framebuffer_update = getattr(VNCConnection.queue_push_framebuffer_update,
                                            'im_func', VNCConnection.queue_push_framebuffer_update)


def connection(rows):
    client = Connection()
    client.server = Server(rows)
    client.options = client.server.options.copy()
    client.pixelformat = PixelFormat()
    client.capabilities = set()
    client.last_rows = {}
    client.zlib_compressor = zlib.compressobj(client.options.zlib_level)
    client.messages = []
    client.write = client.messages.append
    client.log = lambda message: None
    return client


def rectangles(message):
    _, _, count = struct.unpack('>BBH', message[:4])
    offset = 4
    output = []
    for unused in range(count):
        x, y, width, height, encoding = struct.unpack('>HHHHl', message[offset:offset + 12])
        offset += 12
        if encoding == VNCConstants.Encoding_zlib:
            length, = struct.unpack('>L', message[offset:offset + 4])
            offset += 4
            data = message[offset:offset + length]
            offset += length
        else:
            length = width * height * 4
            data = message[offset:offset + length]
            offset += length
        output.append((x, y, width, height, encoding, data))
    return output


class ZlibEncodingTests(unittest.TestCase):
    def test_apple_push_mode_queues_an_incremental_full_region(self):
        client = connection([b'\0\0\0\0'])
        client.width = 1
        client.height = 1
        client.request_regions = Regions()
        client.set_capabilities([VNCConstants.PseudoEncoding_Apple1011])
        self.assertTrue(client.options.push_requests)
        client.queue_push_framebuffer_update()
        self.assertTrue(client.request_regions.pop().incremental)

    def test_incremental_initial_update_sends_all_rows(self):
        client = connection([b'A\0\0\0', b'B\0\0\0', b'C\0\0\0'])
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 3))
        rectangle = rectangles(client.messages[0])[0]
        self.assertEqual((0, 3), (rectangle[1], rectangle[3]))

    def test_incremental_update_sends_only_changed_row_range(self):
        rows = [b'A\0\0\0', b'B\0\0\0', b'C\0\0\0', b'D\0\0\0']
        client = connection(rows)
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 4))
        rows[1] = b'X\0\0\0'
        rows[2] = b'Y\0\0\0'
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 4))
        rectangle = rectangles(client.messages[1])[0]
        self.assertEqual((1, 2), (rectangle[1], rectangle[3]))

    def test_incremental_update_sends_separated_changed_ranges(self):
        rows = [b'A\0\0\0', b'B\0\0\0', b'C\0\0\0', b'D\0\0\0']
        client = connection(rows)
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 4))
        rows[0] = b'X\0\0\0'
        rows[3] = b'Y\0\0\0'
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 4))
        self.assertEqual([(0, 1), (3, 1)],
                         [(rectangle[1], rectangle[3])
                          for rectangle in rectangles(client.messages[1])])

    def test_explicit_nonincremental_request_still_sends_all_rows(self):
        rows = [b'A\0\0\0', b'B\0\0\0', b'C\0\0\0']
        client = connection(rows)
        client.update_framebuffer(RegionRequest(True, 0, 0, 1, 3))
        client.update_framebuffer(RegionRequest(False, 0, 0, 1, 3))
        rectangle = rectangles(client.messages[1])[0]
        self.assertEqual((0, 3), (rectangle[1], rectangle[3]))
    def test_zlib_is_selected_and_length_framed(self):
        client = connection([b'\x01\x02\x03\x00' * 4])
        client.set_capabilities([VNCConstants.Encoding_zlib])
        client.update_framebuffer(RegionRequest(False, 0, 0, 4, 1))
        rectangle = rectangles(client.messages[0])[0]
        self.assertEqual(VNCConstants.Encoding_zlib, rectangle[4])
        self.assertGreater(len(rectangle[5]), 0)

    def test_raw_is_used_without_zlib_capability(self):
        rows = [b'\x01\x02\x03\x00' * 2]
        client = connection(rows)
        client.update_framebuffer(RegionRequest(False, 0, 0, 2, 1))
        rectangle = rectangles(client.messages[0])[0]
        self.assertEqual(VNCConstants.Encoding_Raw, rectangle[4])
        self.assertEqual(rows[0], rectangle[5])

    def test_successive_rectangles_share_a_zlib_stream(self):
        client = connection([b'A\x00\x00\x00', b'B\x00\x00\x00'])
        client.set_capabilities([VNCConstants.Encoding_zlib])
        client.update_framebuffer(RegionRequest(False, 0, 0, 1, 1))
        client.update_framebuffer(RegionRequest(False, 0, 1, 1, 1))
        decompressor = zlib.decompressobj()
        data = []
        for message in client.messages:
            data.append(decompressor.decompress(rectangles(message)[0][5]))
        self.assertEqual(b'A\x00\x00\x00B\x00\x00\x00', b''.join(data))

    def test_zlib_uses_negotiated_pixel_format(self):
        client = connection([b'\x11\x22\x33\x00'])
        client.pixelformat.bpp = 16
        client.pixelformat.depth = 16
        client.pixelformat.redmax = 31
        client.pixelformat.greenmax = 63
        client.pixelformat.bluemax = 31
        client.pixelformat.redshift = 11
        client.pixelformat.greenshift = 5
        client.pixelformat.blueshift = 0
        client.set_capabilities([VNCConstants.Encoding_zlib])
        client.update_framebuffer(RegionRequest(False, 0, 0, 1, 1))
        compressed = rectangles(client.messages[0])[0][5]
        self.assertEqual(b'\x02\x31', zlib.decompressobj().decompress(compressed))

    def test_set_encodings_replaces_old_capabilities(self):
        client = connection([b'\0\0\0\0'])
        client.set_capabilities([VNCConstants.Encoding_zlib])
        client.set_capabilities([])
        self.assertNotIn(VNCConstants.Encoding_zlib, client.capabilities)
