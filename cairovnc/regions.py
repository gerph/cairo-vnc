"""
Management of the redraw regions requested by the client.
"""


class RegionRequest(object):
    """
    Container for a region which the client has requested.
    """

    def __init__(self, incremental, x, y, width, height):
        self.incremental = bool(incremental)
        self.x0 = x
        self.y0 = y
        self.width = width
        self.height = height

    def __repr__(self):
        return "<{}(incremental={}, pos={},{}, size={},{})>".format(self.__class__.__name__,
                                                                    self.incremental,
                                                                    self.x0, self.y0,
                                                                    self.width, self.height)

    @property
    def x1(self):
        return self.x0 + self.width

    @property
    def y1(self):
        return self.y0 + self.height


class Regions(object):
    """
    Manager for the regions.

    Essentially this is a list of region requests, but it may perform coalescing and discarding
    of earlier regions. At present it does not.
    """
    def __init__(self):
        self.regions = []

    def __repr__(self):
        return "<{}({} regions)>".format(self.__class__.__name__,
                                         len(self.regions))

    def __bool__(self):
        return bool(self.regions)
    __nonzero__ = __bool__  # Python 2 compatibility

    def add(self, region):
        self.regions.append(region)

    def pop(self):
        return self.regions.pop(0)
