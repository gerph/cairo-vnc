"""Fixed-format clipboard values used by the RFB extended clipboard protocol."""

import struct

from .errors import CairoVNCBadSurfaceFormatError


try:
    text_type = unicode
except NameError:
    text_type = str


class VNCClipboard(object):
    """Clipboard data keyed by the RFB extended clipboard format constants."""
    Format_Text = 1 << 0
    Format_RTF = 1 << 1
    Format_HTML = 1 << 2
    Format_DIB = 1 << 3
    Formats = (Format_Text, Format_RTF, Format_HTML, Format_DIB)

    def __init__(self, formats=None):
        self.formats = {}
        if formats:
            for clipboard_format, value in formats.items():
                self.set(clipboard_format, value)

    def set(self, clipboard_format, value):
        if clipboard_format not in self.Formats:
            raise ValueError('Unsupported clipboard format {}'.format(clipboard_format))
        if clipboard_format == self.Format_Text:
            if not isinstance(value, text_type):
                value = _as_bytes(value).decode('utf-8')
            self.formats[clipboard_format] = value.replace(u'\r\n', u'\n')
        else:
            value = _as_bytes(value)
            if clipboard_format == self.Format_DIB:
                _validate_dib(value)
            self.formats[clipboard_format] = _as_bytes(value)

    @property
    def text(self):
        return self.formats.get(self.Format_Text)

    @property
    def dib(self):
        """The protocol DIBv5 bytes, without a BITMAPFILEHEADER, if present."""
        return self.formats.get(self.Format_DIB)

    def set_dib(self, data):
        """Set a pre-prepared DIBv5 value and validate its basic structure."""
        self.set(self.Format_DIB, data)
        return self

    def set_dib_surface(self, surface, surface_lock=None):
        """Snapshot an ARGB32 or RGB24 Cairo image surface as a DIBv5."""
        self.set(self.Format_DIB, self.dib_from_surface(surface, surface_lock))
        return self

    @classmethod
    def dib_from_surface(cls, surface, surface_lock=None):
        """Return a top-down 32-bit DIBv5 snapshot of a Cairo image surface."""
        import cairo

        lock = surface_lock or _NullLock()
        with lock:
            width = surface.get_width()
            height = surface.get_height()
            data_format = surface.get_format()
            if data_format not in (cairo.FORMAT_ARGB32, cairo.FORMAT_RGB24):
                raise CairoVNCBadSurfaceFormatError(
                    'Cairo surface format {} is not supported'.format(data_format))
            surface.flush()
            data = _as_bytes(surface.get_data())
            stride = surface.get_stride()

            # BITMAPV5HEADER, with BI_BITFIELDS and the standard BGRA masks.
            header = struct.pack('<IiiHHIIiiIIIIIII36sIIIIIII',
                                 124, width, -height, 1, 32, 3, width * height * 4,
                                 0, 0, 0, 0,
                                 0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000,
                                 0x73524742, b'\0' * 36,
                                 0, 0, 0, 4, 0, 0, 0)
            pixels = bytearray()
            for y in range(height):
                row = data[y * stride:y * stride + width * 4]
                for x in range(0, len(row), 4):
                    blue, green, red, alpha = (_byte_at(row, x), _byte_at(row, x + 1),
                                                _byte_at(row, x + 2), _byte_at(row, x + 3))
                    if data_format == cairo.FORMAT_RGB24:
                        alpha = 255
                    elif alpha:
                        blue = min(255, (blue * 255 + alpha // 2) // alpha)
                        green = min(255, (green * 255 + alpha // 2) // alpha)
                        red = min(255, (red * 255 + alpha // 2) // alpha)
                    else:
                        blue = green = red = 0
                    pixels.extend(bytearray((blue, green, red, alpha)))
            return header + _as_bytes(pixels)

    def format_flags(self):
        flags = 0
        for clipboard_format in self.formats:
            flags |= clipboard_format
        return flags

    def wire_data(self, clipboard_format):
        value = self.formats[clipboard_format]
        if clipboard_format == self.Format_Text:
            return value.replace(u'\n', u'\r\n').encode('utf-8') + b'\0'
        return value


def _as_bytes(value):
    if isinstance(value, bytearray):
        view = memoryview(value)
        if hasattr(view, 'tobytes'):
            return view.tobytes()
        return view.tostring()
    if isinstance(value, text_type):
        raise TypeError('Clipboard binary data must be bytes')
    return b''.join([value])


class _NullLock(object):
    def __enter__(self):
        return self

    def __exit__(self, exctype, excvalue, exctb):
        pass


def _byte_at(data, offset):
    value = data[offset]
    if not isinstance(value, int):
        return ord(value)
    return value


def _validate_dib(value):
    """Reject values that are not a compact 32-bit top-down DIBv5."""
    if len(value) < 124:
        raise ValueError('DIB is shorter than BITMAPV5HEADER')
    (size, width, height, planes, bitcount, compression, image_size) = struct.unpack(
        '<IiiHHII', value[:24])
    if (size != 124 or width <= 0 or height >= 0 or planes != 1 or bitcount != 32 or
            compression != 3 or image_size != width * -height * 4 or
            len(value) != 124 + image_size):
        raise ValueError('DIB must be a compact top-down 32-bit DIBv5')
    if struct.unpack('<IIII', value[40:56]) != (0x00ff0000, 0x0000ff00,
                                                 0x000000ff, 0xff000000):
        raise ValueError('DIB has unsupported colour masks')
