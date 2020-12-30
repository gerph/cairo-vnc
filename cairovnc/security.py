"""
Handlers for the security.
"""

import random
import struct
try:
    import secrets
except ImportError:
    # Secrets isn't available before 3.6
    secrets = None

from .constants import VNCConstants
from .regions import RegionRequest

try:
    import des
except ImportError:
    # We don't have an encryption library.
    des = None


def get_challenge(len):
    """
    Return a random challenge, of a given number of bytes.
    """
    if secrets:
        return secrets.token_bytes(len)
    # random.randbytes only exists from 3.9 onward, by which point we should
    # use secrets, which is already handled.

    return bytes(bytearray([random.randrange(256) for _ in range(len)]))


def des_encrypt(key, value):
    key = des.DesKey(key)
    result = key.encrypt(value)
    return result


# Table for reversing the order of bits in a byte.
bit_reverse = dict((b, ((b & 1)<<7) |
                       ((b & 2)<<5) |
                       ((b & 4)<<3) |
                       ((b & 8)<<1) |
                       ((b & 16)>>1) |
                       ((b & 32)>>3) |
                       ((b & 64)>>5) |
                       ((b & 128)>>7)
                    ) for b in range(256))


def invert_password(password):
    """
    Reverse the bits in the password.

    The VNC protocol's use of DES has the key with bits in the opposite
    order to the expectations of DES.
    """
    return bytes(bytearray(bit_reverse[b] for b in bytearray(password)))


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
        self.name = self.__class__.__name__
        if self.name.startswith('Security'):
            self.name = self.name[8:]

    def log(self, message):
        self.server.log("Security({}): {}".format(self.name, message))

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
        return not self.server.options.password

    def authenticate(self):
        # No security, so we're successful!
        return None


@register_security(VNCConstants.Security_VNCAuthentication)
class SecurityVNCAuthentication(SecurityBase):

    def enabled(self):
        if not des:
            # We cannot authenticate without encryption using the des library
            return False

        return True

    def authenticate(self):
        challenge = get_challenge(16)
        self.server.write(challenge)

        password = self.server.options.password or ''
        # FIXME: I've chosen to use ISO 8859-1 for the password here because that's what the rest of the spec
        #        uses for strings.
        password = password.encode('iso-8859-1')

        # Truncate and pad to 8 characters
        password_padded = password[:8]
        password_padded += b'\x00' * (8 - len(password_padded))
        password_reversed = invert_password(password_padded)

        # encrypt
        expect = des_encrypt(key=password_reversed, value=challenge)
        response = self.server.read(16, timeout=self.server.security_timeout)
        if not response:
            return "Timed out/connection closed during VNC authentication"

        self.log("challenge=%r" % (challenge,))
        self.log("password_padded=%r" % (password_padded,))
        self.log("password_reversed=%r" % (password_reversed,))
        self.log("expected=%r" % (expect,))
        self.log("received=%r" % (response,))

        if expect != response:
            return "Authentication by VNC Authentication failed"

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
