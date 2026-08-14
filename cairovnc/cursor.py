"""Cursor shapes for the RFB Cursor pseudo-encoding."""

from .errors import CairoVNCBadCursorError, CairoVNCBadSurfaceFormatError


class VNCCursor(object):
    """A cursor expressed as canonical CairoVNC BGRX pixels and an RFB mask."""

    def __init__(self, width, height, hotspot, pixels, mask):
        if width <= 0 or height <= 0:
            raise CairoVNCBadCursorError('Cursor dimensions must be positive')
        if len(hotspot) != 2 or not (0 <= hotspot[0] < width and 0 <= hotspot[1] < height):
            raise CairoVNCBadCursorError('Cursor hotspot must be within the cursor')
        if len(pixels) != width * height * 4:
            raise CairoVNCBadCursorError('Cursor pixels must be dense BGRX data')
        mask_length = ((width + 7) // 8) * height
        if len(mask) != mask_length:
            raise CairoVNCBadCursorError('Cursor mask has the wrong length')
        self.width = width
        self.height = height
        self.hotspot = tuple(hotspot)
        self.pixels = _as_bytes(pixels)
        self.mask = _as_bytes(mask)

    @classmethod
    def from_surface(cls, surface, hotspot=(0, 0), surface_lock=None):
        """Snapshot an ARGB32 or RGB24 Cairo image surface as a cursor."""
        import cairo

        lock = surface_lock or _NullLock()
        with lock:
            width = surface.get_width()
            height = surface.get_height()
            data_format = surface.get_format()
            if data_format not in (cairo.FORMAT_ARGB32, cairo.FORMAT_RGB24):
                raise CairoVNCBadSurfaceFormatError('Cairo surface format {} is not supported'.format(data_format))
            data = surface.get_data()
            stride = surface.get_stride()
            pixels = []
            mask = bytearray()
            for y in range(height):
                row = data[y * stride:y * stride + width * 4]
                pixels.append(_as_bytes(row))
                mask_byte = 0
                for x in range(width):
                    if data_format == cairo.FORMAT_RGB24 or _byte_at(row, x * 4 + 3):
                        mask_byte |= 0x80 >> (x % 8)
                    if x % 8 == 7 or x == width - 1:
                        mask.append(mask_byte)
                        mask_byte = 0
            # Cairo ARGB32's fourth byte is alpha. The wire cursor pixels use
            # the normal BGRX representation and visibility comes from mask.
            raw_pixels = b''.join(pixels)
            converted = bytearray(raw_pixels)
            for offset in range(3, len(converted), 4):
                converted[offset] = 0
            return cls(width, height, hotspot, _as_bytes(converted), _as_bytes(mask))


class _NullLock(object):
    def __enter__(self):
        return self

    def __exit__(self, exctype, excvalue, exctb):
        pass


def _as_bytes(data):
    if isinstance(data, bytearray):
        view = memoryview(data)
        if hasattr(view, 'tobytes'):
            return view.tobytes()
        return view.tostring()
    return b''.join([data])


def _byte_at(data, offset):
    value = data[offset]
    if not isinstance(value, int):
        return ord(value)
    return value
