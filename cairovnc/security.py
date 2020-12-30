"""
Handlers for the security.
"""

import struct

from .constants import VNCConstants
from .regions import RegionRequest


security_handlers = {}


def register_security(sectype):
    def register_cls(cls):
        security_handlers[sectype] = cls
        return cls
    return register_cls


class SecurityBase(object):
    """
    Base class from which security objects will be derived.
    """

    def __init__(self, server):
        self.server = server

    def enabled(self):
        """
        Whether this security type is enabled for the server.
        """
        return False

    def authenticate(self):
        """
        Perform the authentication required during connection.

        @return: None if the authentication was successful
                 String describing the authentication failure if it failed
        """
        return "No authentication present"


@register_security(VNCConstants.Security_None)
class SecurityNone(SecurityBase):

    def enabled(self):
        # FIXME: Should only return True if there is no password set
        return True

    def authenticate(self):
        # No security, so we're successful!
        return None


def get_security_types(server):
    """
    Create a dictionary of security objects which are appropriate to this server.
    """

    sectypes = {}
    for sectype, cls in security_handlers.items():
        security = cls(server)
        if security.enabled():
            sectypes[sectype] = security

    return sectypes
