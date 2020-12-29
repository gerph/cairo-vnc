"""
Exception objects for the CairoVNC.
"""

class CairoVNCError(Exception):
    """
    Base error class for problems with the CairoVNC classes
    """
    pass


class CairoVNCBadFormatError(CairoVNCError):
    """
    Raised when the format used by the Cairo surface is not supported.
    """
    pass
