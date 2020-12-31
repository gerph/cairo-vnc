"""
Surface conversion to data that we can return to the user.
"""

import time

import cairo

from .constants import VNCConstants
from .errors import CairoVNCBadSurfaceFormatError


class SurfaceData(object):
    """
    Surface data reads the RGB data from the cairo surface, and converts it to a format
    which is simpler.

    We intentionally cache the results for a period so that multiple clients won't be
    repeatedly requesting the same data (assuming they have a frame rate that's higher
    than the frame rate we're caching at).

    We intentionally convert to a fixed format because each of the clients might want
    a different format. A single format makes it easier to share between them.
    """

    def __init__(self, surface, lock, max_framerate=10):
        """
        Construct an object access to the data on the surface.

        @param surface:         The surface we're getting information on
        @param lock:            A lock to use when accessing the surface
        @param max_framerate:   Maximum speed at which data will be returned
        """
        self.surface = surface
        self.lock = lock
        self.data = None
        self.last_data_time = 0
        self._max_framerate = 1
        self._min_period = 1

        self.max_framerate = max_framerate

        with self.lock:
            self.width = self.surface.get_width()
            self.height = self.surface.get_height()

    @property
    def max_framerate(self):
        return self._max_framerate

    @max_framerate.setter
    def max_framerate(self, value):
        self._max_framerate = value
        self._min_period = 1.0 / value

    @property
    def min_period(self):
        return self._min_period

    @min_period.setter
    def min_period(self, value):
        self._min_period = value
        self._max_framerate = 1.0 / value

    def get_size(self):
        """
        Retrieve just the size of the surface
        """
        return (self.width, self.height)

    def get_data(self):
        """
        Retrieve the data in rows of pixel values in 4-byte B, G, R, 0 sequences.

        @return: tuple of (width, height, list of rows of bytes())
        """
        now = time.time()
        if now - self.last_data_time < self.min_period:
            # This is a request within the frame period, so return the last data we got
            return (self.width, self.height, self.data)

        with self.lock:
            data_format = self.surface.get_format()
            data = self.surface.get_data()
            stride = self.surface.get_stride()

            if data_format == cairo.FORMAT_RGB24:
                def converter(row):
                    return bytes(row)

            elif data_format == cairo.FORMAT_ARGB32:
                def converter(row):
                    # FIXME: Not quite correct; as this leaves the alpha channel in the 4th byte.
                    return bytes(row)
            else:
                # Unrecognised format.
                raise CairoVNCBadSurfaceFormatError("Cairo surface format {} is not supported".format(data_format))

            # We split the returned data into rows as this will be easier for the clients to
            # compare and render only the changes.
            row_data = []
            lastdata = 0    # The prior row's input data
            lastrow = 0     # The prior row's converted data
            for y in range(self.height):
                offset = y * stride
                row = data[offset:offset + stride]
                if row == lastdata:
                    # If it's actually the same as the prior row, then make it the same object
                    row = lastrow
                else:
                    # Copy the data (because the buffer object or memoryview will change after we return)
                    lastdata = row
                    row = converter(row)
                    lastrow = row
                row_data.append(row)

            self.data = row_data

        # It may have taken a long time to process this data, so we'll reset the time
        # it was fetched to the end of the fetch.
        now = time.time()
        self.last_data_time = now

        return (self.width, self.height, self.data)
