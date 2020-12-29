"""
Conversions for the pixel data held by the system.
"""

import struct

from .constants import VNCConstants
from .errors import CairoVNCBadSurfaceFormatError


def converter_null(rowdata):
    return rowdata


class GenericConverter(object):
    """
    A handler for generic conversion of bitmap data.
    """

    def __init__(self, big_endian, bpp, pixel_format):
        self.bpp = bpp
        self.big_endian = big_endian
        self.width = -1
        self.in_format = ''
        self.out_format = ''
        self.pixel_format = pixel_format

    def __call__(self, rowdata):
        """
        Convert from little endian 0x??RRGGBB to correct endianness and bitness.
        """
        width = int(len(rowdata) / 4)
        if width != self.width:
            if self.bpp == 32:
                pack_format = 'L'
            elif self.bpp == 16:
                pack_format = 'H'
            elif self.bpp == 8:
                pack_format = 'B'
            else:
                raise CairoVNCBadPixelFormatError("PixelFormat for {} bit data is not supported".format(self.width))
            in_format = '<' + ('L' * width)
            if self.big_endian:
                out_format = '>' + (pack_format * width)
            else:
                out_format = '<' + (pack_format * width)
            self.in_format = in_format
            self.out_format = out_format
            self.width = width

        in_words = struct.unpack(self.in_format, rowdata)
        out_words = []
        for word in in_words:
            r = (word>>16) & self.pixel_format.redmax
            g = (word>>8) & self.pixel_format.greenmax
            b = (word>>0) & self.pixel_format.bluemax
            word = ((r<<self.pixel_format.redshift) |
                    (g<<self.pixel_format.greenshift) |
                    (b<<self.pixel_format.blueshift))
            out_words.append(word)

        out_data = struct.pack(self.out_format, *out_words)
        return out_data


class PixelFormat(object):
    # Default parameters
    bpp = 32
    depth = 24
    endianness = VNCConstants.PixelFormat_LittleEndian
    truecolour = VNCConstants.PixelFormat_TrueColour
    redmax = 255
    greenmax = 255
    bluemax = 255
    redshift = 16
    greenshift = 8
    blueshift = 0
    padding = b'\x00\x00\x00'

    def __init__(self, data=None):
        """
        Pixel Format data structure representation.

        See 7.4 Pixel Format Data Structure.
        """
        self._converter = None
        if data:
            self.decode(data)

    def __repr__(self):
        return "<{}({} bpp, {} red(shift {}), {} green(shift {}), {} blue(shift {}))>".format(self.__class__.__name__,
                                                                                              self.bpp,
                                                                                              self.redmax, self.redshift,
                                                                                              self.greenmax, self.greenshift,
                                                                                              self.bluemax, self.blueshift)

    def decode(self, data):
        (self.bpp, self.depth, bigendian, truecolour,
         self.redmax, self.greenmax, self.bluemax,
         self.redshift, self.greenshift, self.blueshift,
         _) = struct.unpack('>BBBBHHHBBB3s', data)
        self.truecolour = VNCConstants.PixelFormat_TrueColour if truecolour else VNCConstants.PixelFormat_Paletted
        self.endianness = VNCConstants.PixelFormat_BigEndian if bigendian else VNCConstants.PixelFormat_LittleEndian
        self._converter = None

    def encode(self):
        data = struct.pack('>BBBBHHHBBB3s',
                           self.bpp, self.depth, self.endianness, self.truecolour,
                           self.redmax, self.greenmax, self.bluemax,
                           self.redshift, self.greenshift, self.blueshift,
                           self.padding)
        return data

    @property
    def converter(self):
        """
        Return a function which will convert from the internal format we're using to what they requested.

        The converter should be passed a row of data as a bytes object.
        """
        if self._converter:
            return self._converter

        if not self.truecolour:
            raise CairoVNCBadPixelFormatError("Paletted PixelFormats are not supported")

        if self.bpp == 32 and \
           self.endianness == VNCConstants.PixelFormat_LittleEndian and \
           self.redmax == 255 and self.redshift == 16 and \
           self.greenmax == 255 and self.greenshift == 8 and \
           self.bluemax == 255 and self.blueshift == 0:
            # This is the same format as our internal data, so it's a pass through
            self._converter = converter_null

        elif self.bpp == 32 and \
             self.endianness == VNCConstants.PixelFormat_BigEndian and \
             self.redmax == 255 and self.redshift == 8 and \
             self.greenmax == 255 and self.greenshift == 16 and \
             self.bluemax == 255 and self.blueshift == 24:
            # This means the exact same thing, but represented in bigendian words
            self._converter = converter_null

        else:
            self._converter = GenericConverter(self.endianness == VNCConstants.PixelFormat_BigEndian, self.bpp,
                                               self)

        return self._converter
