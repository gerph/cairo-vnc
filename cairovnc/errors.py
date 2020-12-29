"""
Exception objects for the CairoVNC.
"""

class CairoVNCError(Exception):
    """
    Base error class for problems with the CairoVNC classes
    """
    pass


class CairoVNCBadPixelFormatError(CairoVNCError):
    """
    Raised when the PixelFormat used by the client is not supported.
    """
    pass


class CairoVNCBadSurfaceFormatError(CairoVNCError):
    """
    Raised when the format used by the Cairo surface is not supported.
    """
    pass
