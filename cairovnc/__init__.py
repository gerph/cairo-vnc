"""
Cairo surface served over VNC.

Usage:

    import cairovnc
    options = cairovnc.CairoVNCOptions(port=5900)
    server = cairovnc.CairoVNCServer(surface=surface, options=options)
    server.serve_forever()
"""

import array
import fcntl
try:
    import queue
except ImportError:
    import Queue as queue
import select
import struct
try:
    import socketserver
except ImportError:
    # Python 2 compatibility.
    import SocketServer as socketserver
import termios
import threading
import time
import traceback
import zlib

from .constants import VNCConstants
from .surfacedata import SurfaceData
from .pixeldata import PixelFormat
from .clientmsg import dispatch_msg
from .regions import Regions, RegionRequest
from .security import get_security_types
from .cursor import VNCCursor
from .clipboard import VNCClipboard
from .events import VNCEventClipboard


__version__ = '0.2.0'


def clipboard_value(value):
    """Normalise legacy text or a VNCClipboard instance to VNCClipboard."""
    if isinstance(value, VNCClipboard):
        return value
    return VNCClipboard({VNCClipboard.Format_Text: value})


class CairoVNCOptions(object):
    """
    A container object holding the options that can be set on a server and connection.

    Simple options are available on the constructor. More advanced options are properties.
    """

    def __init__(self, host='0.0.0.0', port=5900, password=None, password_readonly=None, display_name='Cairo'):
        self.host = host
        self.port = port

        # Set password to None to allow any connections (although the macOS screen sharing
        # hangs if you do this).
        self.password = password
        # A dedicated password that allows readonly access
        self.password_readonly = password_readonly

        # The name of the display
        self.display_name = display_name

        # The maximum number of clients which we'll allow (or None for no limit)
        self.max_clients = 2

        # The maximum speed at which we will deliver frame updates, regardless of what the
        # clients request.
        self.max_framerate = 20

        # Whether the access is read-only, or allows input events
        # We default to read_only, so that simple uses of the client don't end up blocking
        # when the queue becomes full.
        # But if they explicitly set both types of password, then they wanted a differentiated
        # server, so we clear the readonly flag.
        self.read_only = True ^ bool(password and password_readonly)

        # How many events we'll allow to queue before blocking (use 0 for infinite)
        # The default here is enough that it should not block too quickly, and small
        # enough that we don't gobble memory.
        self.event_queue_length = 500

        # Whether we will push frames when the client says that the surface has changed.
        # This is a protocol violation, because FrameUpdate messages from the server are only
        # meant to be sent in response to FrameUpdateRequest messages from the client.
        # However, the Apple Screen Sharing client doesn't update at all unless you push
        # requests.
        # This is detected by an Apple-specific encoding being supplied in the capabilities
        # of the client, but it could be enabled for all clients.
        self.push_requests = False

        # Whether we're giving log output of what's happening
        self.verbose = False

        # The zlib compression level used for framebuffer rectangles.
        self.zlib_level = 6

        # Maximum uncompressed size accepted for each extended clipboard format.
        self.clipboard_maximum_size = 20 * 1024 * 1024

    def copy(self):
        obj = CairoVNCOptions(host=self.host,
                              port=self.port,
                              password=self.password,
                              password_readonly=self.password_readonly,
                              display_name=self.display_name)

        # Copy the less common options
        obj.max_clients = self.max_clients
        obj.max_framerate = self.max_framerate
        obj.read_only = self.read_only
        obj.event_queue_length = self.event_queue_length
        obj.push_requests = self.push_requests
        obj.verbose = self.verbose
        obj.zlib_level = self.zlib_level
        obj.clipboard_maximum_size = self.clipboard_maximum_size

        return obj


class CommStream(object):
    """
    A communication stream.

    Replace this for encrypted traffic.
    """
    default_timeout = 2

    def __init__(self, sock):
        self.sock = sock
        self.closed = False
        self.data = []
        self.datalen = 0
        self.fionread_data = array.array('i', [0])

    def log(self, message):
        #print("Comm: {}".format(message))
        pass

    def readdata(self, nbytes):
        """
        Read data from the socket - may be overridden to decrypt the data from the wire
        """
        if self.closed:
            # If the connection was closed; we didn't get any data
            return b''
        return self.sock.recv(nbytes)

    def writedata(self, data):
        """
        Write data to the socket - may be overridden to encrypt the data on the wire
        """
        if self.closed:
            return
        #self.log("Sending %r" % (data,))
        try:
            self.sock.send(data)
        except Exception:
            # Any failure here is almost certainly fatal; mark the connection as closed
            self.closed = True

    def fionread(self):
        if self.closed:
            # If we were closed, then report that we have no data
            return -1
        if fcntl.ioctl(self.sock, termios.FIONREAD, self.fionread_data) == -1:
            # Any error means the connection is closed
            self.closed = True
            return -1
        return struct.unpack('I', self.fionread_data)[0]

    def read_upto(self, terminator, timeout=None):
        """
        Read data until we hit a terminator, or timeout.

        @param terminator:  Terminating string
        @param timeout:     Timeout in seconds

        @return: string before terminator, or None if timed out
        """
        if not timeout:
            timeout = self.default_timeout
        endtime = time.time() + timeout
        if self.data:
            # The data might already be present
            current_data = b''.join(self.data)
            index = current_data.find(terminator)
            if index != -1:
                self.data = [current_data[index + len(terminator):]]
                self.datalen = len(self.data[0])
                return current_data[:index]
            self.data = []
            self.datalen = 0
        else:
            current_data = b''

        while not self.closed:
            timeout = endtime - time.time()
            if timeout <= 0:
                break
            (rlist, wlist, xlist) = select.select([self.sock], [], [], timeout)
            if rlist:
                nbytes = self.fionread()
                if nbytes <= 0:
                    self.closed = True
                    break
                current_data += self.readdata(nbytes)
                index = current_data.find(terminator)
                if index != -1:
                    self.data = [current_data[index + len(terminator):]]
                    self.datalen = len(self.data[0])
                    return current_data[:index]

        self.data = [current_data]
        self.datalen = len(current_data)
        return None

    def read_nbytes(self, size, timeout=None):
        """
        Read a fixed number of bytes, or timeout.

        @param size:    number of bytes to read
        @param timeout:     Timeout in seconds

        @return: bytes read, or None if timed out
        """
        if not timeout:
            timeout = self.default_timeout
        endtime = time.time() + timeout
        data = []
        while not self.closed:
            timeout = endtime - time.time()
            if timeout <= 0:
                break

            while self.datalen >= size and size != 0:
                first = self.data[0]
                if len(first) <= size:
                    data.append(first)
                    self.data.pop(0)
                    self.datalen -= len(first)
                    size -= len(first)
                else:
                    data.append(first[:size])
                    self.data[0] = first[size:]
                    self.datalen -= size
                    size = 0
            if size == 0:
                break

            # Put more data into the buffer
            self.log("Awaiting %i bytes (got %r, buffered %r, datalen %r)" % (size, data, self.data, self.datalen))
            (rlist, wlist, xlist) = select.select([self.sock], [], [], timeout)
            if rlist:
                nbytes = self.fionread()
                if nbytes <= 0:
                    # Connection was closed
                    self.closed = True
                    break
                self.log("Reading %i bytes" % (nbytes,))
                got = self.readdata(nbytes)
                self.data.append(got)
                self.datalen += len(got)

        data = b''.join(data)
        if size:
            # We timed out before all the data was read. Put what we have back at the start
            # of the buffer.
            if data:
                self.data.insert(0, data)
            return None

        return data


class VNCConnection(socketserver.BaseRequestHandler):
    """
    A VNCConnection handles communication with one client.
    """

    # Timeout for automated transactions during the connection phases
    connect_timeout = 10

    # Timeout for negotiation with the user during the security phase.
    # Intentionally longer because this is where the user may type a password, etc.
    security_timeout = 60

    # How regularly we check for changes in our local state (eg screen size, clipboard, etc)
    client_timeout = 0.25

    # Timeout for receiving any payload data once we know that we're receiving data from client
    payload_timeout = 5

    def setup(self):
        """
        Set up variables for a remote connection which is about to start.

        Thread: Connection thread
        """
        self.connected = False
        self.stream = CommStream(self.request)

        self.pixelformat = PixelFormat()
        self.pixelformat.bpp = 32
        self.pixelformat.depth = 24
        self.pixelformat.endianness = VNCConstants.PixelFormat_LittleEndian
        self.pixelformat.truecolour = VNCConstants.PixelFormat_TrueColour
        self.pixelformat.redmax = 255
        self.pixelformat.greenmax = 255
        self.pixelformat.bluemax = 255
        self.pixelformat.redshift = 16
        self.pixelformat.greenshift = 8
        self.pixelformat.blueshift = 0

        # Current framebuffer size
        self.width = None
        self.height = None

        self.protocol = None
        self.sectype = None
        self.security = None

        # Button states
        self.pointer_buttons = 0
        self.pointer_wire_buttons = 0
        self.pointer_xpos = -1
        self.pointer_ypos = -1

        # We copy the options because they might be changed by security or other interaction.
        self.options = self.server.options.copy()

        # Changes that are pending
        self.changed_display = False
        self.changed_name = False
        self.changed_clipboard = self.server.clipboard is not None
        self.extended_clipboard_capabilities = None
        self.extended_clipboard_limits = {}

        # The capabilities for communicating with the client
        self.capabilities = set([])

        # FrameUpdate variables
        self.request_regions = Regions()
        self.last_rows = {}
        self.min_frame_period = 1.0 / self.options.max_framerate
        self.last_frameupdate_time = 0          # When we last sent a frame update
        self.last_frameupdaterequest_time = 0   # When they last requested a frame update
        self.last_frameupdate_push_time = 0     # When the oldest pending frameupdate push was requested
        self.changed_frame = False              # Whether there's a push pending
        self.zlib_compressor = zlib.compressobj(self.options.zlib_level)
        self.changed_cursor = True

    def handle(self):
        """
        Handle a connection from a remote server.

        Thread: Connection thread
        """
        self.log("Connection received")
        if not self.server.client_connected(self):
            # Connection was denied; we'll just return immediately
            return
        self.connected = True
        try:
            self.do_vnc_protocol()
        except Exception as exc:
            self.log_exception(exc)

    def finish(self):
        """
        Clean up after the connection has been handled.

        Thread: Connection thread
        """
        if self.connected:
            # We only notify the server object that we disconnected if we had said we were connected
            self.server.client_disconnected(self)
        self.stream.closed = True

    def disconnect(self):
        """
        Request to disconnect this client.

        Thread: Any thread
        """
        # We flag this by treating the stream as closed, so that we exit our handling loop
        self.stream.closed = True

    def read(self, size, timeout):
        """
        Read a number of bytes from the connection.

        Thread: Connection thread
        """
        return self.stream.read_nbytes(size, timeout=timeout)

    def write(self, data):
        """
        Write data to the connection, blocking until all the data is sent.

        Thread: Connection thread
        """
        return self.stream.writedata(data)

    def log(self, message):
        """
        Log a message to the server object.

        Thread: Connection thread
        """
        self.server.client_log(self, message)

    def log_exception(self, exc):
        """
        An exception occurred during processing; log any details necessary.

        Thread: Connection thread
        """
        self.log("Exception: {}: {}".format(exc.__class__.__name__,
                                            exc))
        for line in traceback.format_exc().splitlines():
            self.log(line)

    def do_protocol(self):
        """
        7.1.1 ProtocolVersion Handshake

        Announce ourselves, and find out what protocol they want to speak.

        Thread: Connection thread

        @return: True if we were successful; False if something went wrong.
        """
        self.stream.writedata(b'RFB 003.008\n')

        protocol_handshake = self.stream.read_upto(terminator=b'\n', timeout=self.connect_timeout)
        if not protocol_handshake:
            # FIXME: Report failed connection?
            return False

        self.log("Protocol handshake: {!r}".format(protocol_handshake))
        if not protocol_handshake.startswith(b'RFB 003'):
            self.log("Don't understand the protocol. Giving up.")
            # FIXME: Report the failure
            return False

        self.protocol = protocol_handshake[4:]
        return True

    def do_security(self):
        """
        7.1.2. Security handshake

        Negotiate authentication and security protocols.

        Thread: Connection thread

        @return: True if we were successful; False if something went wrong.
        """

        # Obtain all the security types suitable for this server/client
        security_types = get_security_types(self)

        if not security_types:
            # There are no security types available
            self.log("Configuration error: No security types available, disconnecting")
            return

        if self.protocol >= b'003.007':
            security_supported = sorted(security_types)  # Make the types given deterministic
            security_data = [len(security_supported)]
            security_data.extend(security_supported)
            data = bytearray(security_data)
            self.stream.writedata(data)

            response = self.read(1, timeout=self.connect_timeout)
            if not response:
                # Timeout, or disconnect
                self.log("Timed out at Security Handshake")
                return False
            self.sectype = bytearray(response)[0]
        else:
            if VNCConstants.Security_VNCAuthentication in security_types:
                self.sectype = VNCConstants.Security_VNCAuthentication
            else:
                self.sectype = VNCConstants.Security_None
            data = struct.pack('>I', self.sectype)
            self.stream.writedata(data)

        self.security = security_types.get(self.sectype, None)
        if self.security is None:
            self.log("Invalid security type: {}".format(self.sectype))
            return False

        self.log("Security: {}".format(self.security.name))
        failed = self.security.authenticate()
        self.log("Security result: %r" % (failed or 'Success',))

        # For 'No encryption' there isn't a SecurityResult prior to 3.8
        has_security_result = (self.protocol >= b'003.008' or self.sectype != VNCConstants.Security_None)
        if has_security_result:
            # 7.1.3. SecurityResult Handshake
            if failed:
                data = struct.pack('>L', VNCConstants.SecurityResult_Failed)
                if self.protocol >= b'003.008':
                    data += struct.pack('>L', len(failed)) + failed.encode('iso-8859-1')
            else:
                data = struct.pack('>L', VNCConstants.SecurityResult_OK)
            self.stream.writedata(data)

        if failed:
            self.log("Security failed, disconnecting")
            return False

        return True

    def do_clientinit(self):
        """
        7.3.1. ClientInit

        Read their requested access.

        Thread: Connection thread

        @return: True if we were successful; False if something went wrong.
        """
        response = self.read(1, timeout=self.connect_timeout)
        if not response:
            # Timeout, or disconnect
            self.log("Timed out at ClientInit")
            # FIXME: Report the failure
            return False

        (shared_flag,) = struct.unpack('B', response)
        # FIXME: Do we want to honour this or just ignore it?
        if shared_flag == VNCConstants.ClientInit_Exclusive:
            self.log("ClientInit: Requested exclusive access (denied, as not supported)")

        return True

    def do_serverinit(self):
        """
        7.3.2. ServerInit

        Report the initial framebuffer configuration and server name.

        Thread: Connection thread

        @return: True if we were successful; False if something went wrong.
        """
        (width, height, rows) = self.server.surface_data()
        self.width = width
        self.height = height
        name = self.server.options.display_name

        data_size = struct.pack('>HH', width, height)
        data_pixelformat = self.pixelformat.encode()
        name_encoded = name.encode('utf-8')
        data_name = struct.pack('>L', len(name_encoded)) + name_encoded
        data = data_size + data_pixelformat + data_name
        self.log("ServerInit message: %r" % (data,))
        self.stream.writedata(data)

        return True

    def do_vnc_protocol(self):
        """
        Run through the VNC protocol.

        Thread: Connection thread

        We return when the connection has been closed or some invalid operation was performed.
        """
        # 7.1.1. ProtocolVersion Handshake
        if not self.do_protocol():
            return

        # 7.1.2. Security handshake
        if not self.do_security():
            return

        # 7.3.1. ClientInit
        if not self.do_clientinit():
            return

        # 7.3.2. ServerInit
        if not self.do_serverinit():
            return

        # Now we read messages from the client
        while not self.stream.closed:
            timeout = self.client_timeout
            if self.request_regions or self.options.push_requests:
                timeout = time.time() - self.last_frameupdate_time
                if timeout < 0:
                    timeout = 0
            response = self.read(1, timeout=timeout)
            if response:
                msgtype = bytearray(response)[0]
                handled = dispatch_msg(msgtype, self)
                if not handled:
                    # Something went wrong; so we're done with this connection
                    break

            if self.changed_frame:
                if self.options.push_requests:
                    # There is a changed frame request pending, and we have push requests enabled.
                    if not self.request_regions:
                        if time.time() - self.last_frameupdate_time >= self.min_frame_period:
                            # Add a request for a full redraw
                            self.request_regions.add(RegionRequest(incremental=False,
                                                                   x=0, y=0,
                                                                   width=self.width, height=self.height))
                            self.changed_frame = False
                else:
                    # They don't want push requests, so we can clear the flag
                    self.changed_frame = False

            if self.changed_display:
                # There was a notification that the display was changed, so we may
                # need to update the framebuffer.
                # 7.8.2. DesktopSize Pseudo-Encoding
                (new_width, new_height) = self.server.surface_size()
                if new_width != self.width or new_height != self.height:
                    if VNCConstants.PseudoEncoding_DesktopSize in self.capabilities:
                        # We can only send the new desktop size if it's in the capabilities.
                        msg = struct.pack('>BBHHHHHl',
                                          VNCConstants.ServerMsgType_FramebufferUpdate, 0,
                                          1,  # one rectangle update
                                          0, 0, new_width, new_height,
                                          VNCConstants.PseudoEncoding_DesktopSize)
                        self.log("Notify of DesktopSize {}x{}".format(new_width, new_height))
                        self.write(msg)

                        # Assume that we have to deliver the entire buffer
                        self.last_rows = {}
                    else:
                        self.log("Client cannot receive DesktopSize {}x{}".format(new_width, new_height))

                    # Any updates that are pending will be irrelevant now, but if there
                    # are any they should be replaced by a full redraw.
                    if self.request_regions:
                        self.request_regions.clear()
                        self.request_regions.add(RegionRequest(incremental=False,
                                                               x=0, y=0,
                                                               width=new_width, height=new_height))
                        # We should expect the client to send a request for the whole screen
                        # on receipt of the DesktopSize message, BUT the above message ensures
                        # that if they had an outstanding FramebufferUpdate sent, there remains
                        # a response to it. Otherwise the client might get stuck believing there
                        # is a request pending and not sending another.
                self.width = new_width
                self.height = new_height
                self.changed_display = False
                # Force the update to happen as soon as possible
                self.last_frameupdate_time = 0

            if self.changed_name:
                if self.options.display_name != self.server.options.display_name:
                    name_encoded = self.server.options.display_name.encode('utf-8')
                    if VNCConstants.PseudoEncoding_DesktopName in self.capabilities:
                        # We can only send the new desktop name if it's in the capabilities.
                        # Support for name changing is variable between clients.
                        msg = struct.pack('>BBHHHHHl',
                                          VNCConstants.ServerMsgType_FramebufferUpdate, 0,
                                          1,  # one rectangle update
                                          0, 0, 0, 0,  # x,y,width,height must be 0
                                          VNCConstants.PseudoEncoding_DesktopName)
                        data_name = struct.pack('>L', len(name_encoded)) + name_encoded
                        msg += data_name
                        self.log("Notify of DesktopName {}".format(name_encoded))
                        self.write(msg)
                    else:
                        self.log("Client cannot receive DesktopName {}".format(name_encoded))

                    self.options.display_name = self.server.options.display_name
                self.changed_name = False

            if self.changed_clipboard:
                self.send_clipboard()
                self.changed_clipboard = False

            # If they requested some region to be drawn, so we should dispatch
            # a frame buffer update.
            # This throttling ensures that we won't be repeatedly trying to get data
            # from the cairo buffer (which should already be protected by the surfacedata
            # caching) and then comparing it for delivery to the client (which is not
            # otherwise protected, and can be quite involved)
            # Without this throttling, the server works as fast as it can, with the
            # client requesting data as fast as it can.
            if time.time() - self.last_frameupdate_time >= self.min_frame_period:
                # Don't update more often than the frame period
                while self.request_regions:
                    region = self.request_regions.pop()
                    self.update_framebuffer(region)
                self.last_frameupdate_time = time.time()

    def update_framebuffer(self, region):
        """
        Framebuffer updates here use only whole rows, because we're lazy here.

        Thread: Connection thread
        """
        (width, height, surface_rows) = self.server.surface_data()
        if not region.incremental:
            # Redraw the whole screen because it's not incremental
            # The range list is a tuple of (row number start, the number of rows to draw)
            redraw_range = [(region.y0, region.height)]
        else:
            redraw_range = []
            diff_start = None
            diff_size = 0
            for y in range(region.y0, region.y0 + region.height):
                if y < len(surface_rows):
                    rowdata = surface_rows[y]
                else:
                    # Skip the rows if they are not present in the framebuffer
                    continue
                diff = rowdata != self.last_rows.get(y, None)
                if diff:
                    if diff_start is not None:
                        diff_size += 1
                    else:
                        diff_start = y
                        diff_size = 1
                else:
                    if diff_start is not None:
                        redraw_range.append((diff_start, diff_size))
                        diff_start = None
            if diff_start is not None:
                redraw_range.append((diff_start, diff_size))

        send_cursor = (getattr(self, 'changed_cursor', False) and
                       getattr(self.server, 'cursor', None) and
                       VNCConstants.PseudoEncoding_Cursor in self.capabilities)
        nrects = len(redraw_range) + int(bool(send_cursor))
        msg_data = [struct.pack('>BBH', VNCConstants.ServerMsgType_FramebufferUpdate,
                                        0,
                                        nrects)]
        if nrects:
            self.log("FramebufferUpdate: {} rectangles to send".format(nrects))
            for y0, rows in redraw_range:

                raw_data = []
                for y in range(y0, y0 + rows):
                    raw_data.append(self.pixelformat.converter(surface_rows[y]))
                    self.last_rows[y] = surface_rows[y]

                raw_data = b''.join(raw_data)
                encoding = VNCConstants.Encoding_Raw
                rows_data = [struct.pack('>HHHHl', 0, y0, width, rows, encoding)]
                if VNCConstants.Encoding_zlib in self.capabilities:
                    encoding = VNCConstants.Encoding_zlib
                    compressed_data = self.zlib_compressor.compress(raw_data)
                    compressed_data += self.zlib_compressor.flush(zlib.Z_SYNC_FLUSH)
                    rows_data[0] = struct.pack('>HHHHl', 0, y0, width, rows, encoding)
                    rows_data.append(struct.pack('>L', len(compressed_data)))
                    rows_data.append(compressed_data)
                    self.log("    Sending rows {} - {} as zlib: {} raw bytes, {} compressed bytes".format(
                        y0, y0 + rows, len(raw_data), len(compressed_data)))
                else:
                    rows_data.append(raw_data)
                    self.log("    Sending rows {} - {} as Raw: {} bytes".format(
                        y0, y0 + rows, len(raw_data)))

                msg_data.extend(rows_data)

            if send_cursor:
                cursor = self.server.cursor
                pixels = []
                for y in range(cursor.height):
                    offset = y * cursor.width * 4
                    pixels.append(self.pixelformat.converter(
                        cursor.pixels[offset:offset + cursor.width * 4]))
                msg_data.append(struct.pack('>HHHHl', cursor.hotspot[0], cursor.hotspot[1],
                                            cursor.width, cursor.height,
                                            VNCConstants.PseudoEncoding_Cursor))
                msg_data.append(b''.join(pixels))
                msg_data.append(cursor.mask)
                self.log("    Sending Cursor {}x{} at {},{}".format(
                    cursor.width, cursor.height, cursor.hotspot[0], cursor.hotspot[1]))
                self.changed_cursor = False

        msg = b''.join(msg_data)
        self.write(msg)

    def set_capabilities(self, capabilities):
        """
        Update the capabilities used by this client.

        Thread: Connection thread

        @param capabilities: A list of the encodings that the client is capable of
        """
        self.capabilities = set(capabilities)
        self.options.push_requests = (self.server.options.push_requests or
                                      VNCConstants.PseudoEncoding_Apple1011 in self.capabilities)
        if VNCConstants.PseudoEncoding_ExtendedClipboard in self.capabilities:
            self.send_extended_clipboard_capabilities()

    def send_clipboard(self):
        """Send the server's current clipboard in the client's supported form."""
        if VNCConstants.PseudoEncoding_ExtendedClipboard in self.capabilities:
            self.send_extended_clipboard_action(VNCConstants.Clipboard_Action_Notify,
                                                self.server.clipboard.format_flags())
            return

        # Clients without the extension retain the standard Latin-1 behaviour.
        text = self.server.clipboard
        if text is None or text.text is None:
            return
        try:
            text_encoded = text.text.encode('iso-8859-1')
        except UnicodeEncodeError:
            self.log("ServerCutText: clipboard text cannot be represented in ISO-8859-1")
            return
        message = struct.pack('>B3sL', VNCConstants.ServerMsgType_ServerCutText,
                              b'\0\0\0', len(text_encoded)) + text_encoded
        self.log("ServerCutText: textlen=%i, text=%r" % (len(text_encoded), text.text))
        self.write(message)

    def send_extended_clipboard_message(self, flags, data=b''):
        """Send an extended ServerCutText message with a bounded payload."""
        payload = struct.pack('>L', flags) + data
        message = struct.pack('>B3sl', VNCConstants.ServerMsgType_ServerCutText,
                              b'\0\0\0', -len(payload)) + payload
        self.write(message)

    def send_extended_clipboard_capabilities(self):
        """Advertise the extended clipboard formats and actions this server supports."""
        formats = VNCClipboard.Format_Text | VNCClipboard.Format_RTF | VNCClipboard.Format_HTML
        actions = (VNCConstants.Clipboard_Action_Caps |
                   VNCConstants.Clipboard_Action_Request |
                   VNCConstants.Clipboard_Action_Peek |
                   VNCConstants.Clipboard_Action_Notify |
                   VNCConstants.Clipboard_Action_Provide)
        # Zero unsolicited sizes require an explicit notify/request exchange.
        sizes = struct.pack('>LLL', 0, 0, 0)
        self.send_extended_clipboard_message(formats | actions |
                                             VNCConstants.Clipboard_Action_Caps,
                                             zlib.compress(sizes))

    def send_extended_clipboard_action(self, action, formats):
        self.send_extended_clipboard_message(action | formats)

    def send_extended_clipboard_provide(self, requested_formats):
        clipboard = self.server.clipboard
        formats = clipboard.format_flags() & requested_formats
        data = []
        for clipboard_format in range(16):
            bit = 1 << clipboard_format
            if formats & bit:
                value = clipboard.wire_data(bit)
                data.append(struct.pack('>L', len(value)))
                data.append(value)
        self.send_extended_clipboard_message(formats | VNCConstants.Clipboard_Action_Provide,
                                             zlib.compress(b''.join(data)))

    def receive_extended_clipboard(self, payload):
        """Process an already-read extended ClientCutText payload."""
        if VNCConstants.PseudoEncoding_ExtendedClipboard not in self.capabilities:
            self.log("ClientCutText: extended clipboard was not negotiated")
            return
        if len(payload) < 4:
            self.log("ClientCutText: short extended clipboard payload")
            return
        flags, = struct.unpack('>L', payload[:4])
        formats = flags & 0xffff
        action = flags & 0x1f000000
        data = payload[4:]
        if flags & VNCConstants.Clipboard_Action_Caps:
            self.receive_extended_clipboard_capabilities(formats, data)
        elif action == VNCConstants.Clipboard_Action_Request:
            self.send_extended_clipboard_provide(formats)
        elif action == VNCConstants.Clipboard_Action_Peek:
            self.send_extended_clipboard_action(VNCConstants.Clipboard_Action_Notify,
                                                self.server.clipboard.format_flags())
        elif action == VNCConstants.Clipboard_Action_Notify:
            self.send_extended_clipboard_action(VNCConstants.Clipboard_Action_Request,
                                                formats & self.extended_clipboard_formats())
        elif action == VNCConstants.Clipboard_Action_Provide:
            self.receive_extended_clipboard_provide(formats, data)
        else:
            self.log("ClientCutText: unsupported extended clipboard flags &{:x}".format(flags))

    def extended_clipboard_formats(self):
        return VNCClipboard.Format_Text | VNCClipboard.Format_RTF | VNCClipboard.Format_HTML

    def receive_extended_clipboard_capabilities(self, formats, data):
        expected = 4 * sum(1 for bit in range(16) if formats & (1 << bit))
        decoded = self.decompress_extended_clipboard(data, expected)
        if decoded is None or len(decoded) != expected:
            self.log("ClientCutText: invalid extended clipboard capabilities")
            return
        self.extended_clipboard_capabilities = formats
        self.extended_clipboard_limits = {}
        offset = 0
        for bit in range(16):
            if formats & (1 << bit):
                maximum, = struct.unpack('>L', decoded[offset:offset + 4])
                self.extended_clipboard_limits[1 << bit] = maximum
                offset += 4

    def receive_extended_clipboard_provide(self, formats, data):
        decoded = self.decompress_extended_clipboard(data, self.options.clipboard_maximum_size * 3 + 64)
        if decoded is None:
            return
        values = {}
        offset = 0
        for bit in range(16):
            if formats & (1 << bit):
                if offset + 4 > len(decoded):
                    self.log("ClientCutText: truncated extended clipboard format")
                    return
                size, = struct.unpack('>L', decoded[offset:offset + 4])
                offset += 4
                if size > self.options.clipboard_maximum_size or offset + size > len(decoded):
                    self.log("ClientCutText: oversized extended clipboard format")
                    return
                if (1 << bit) in VNCClipboard.Formats:
                    value = decoded[offset:offset + size]
                    if (1 << bit) == VNCClipboard.Format_Text:
                        if not value.endswith(b'\0'):
                            self.log("ClientCutText: UTF-8 clipboard text has no terminating null")
                            return
                        value = value[:-1]
                    values[1 << bit] = value
                offset += size
        if offset != len(decoded):
            self.log("ClientCutText: trailing extended clipboard data")
            return
        try:
            clipboard = VNCClipboard(values)
        except (TypeError, UnicodeDecodeError, ValueError) as exc:
            self.log("ClientCutText: invalid extended clipboard data: {}".format(exc))
            return
        if clipboard.formats and not self.options.read_only:
            self.queue_event(VNCEventClipboard(clipboard))

    def decompress_extended_clipboard(self, data, maximum_size):
        try:
            decompressor = zlib.decompressobj()
            decoded = decompressor.decompress(data, maximum_size + 1)
            if decompressor.unconsumed_tail:
                raise ValueError('decompressed data exceeds the maximum size')
            decoded += decompressor.flush()
            if len(decoded) > maximum_size or decompressor.unused_data:
                raise ValueError('invalid compressed data')
            return decoded
        except (ValueError, zlib.error) as exc:
            self.log("ClientCutText: invalid extended clipboard compression: {}".format(exc))
            return None

    def queue_event(self, event):
        """
        Insert an event into the queue for the animator.

        Thread: Connection thread

        @param event:   A VNCEvent to put on the queue.
        """
        self.server.event_queue.put(event)

    def change_surface(self):
        """
        The display surface has changed, so we might need to issue a DesktopSize.

        Thread: Off connection thread
        """
        self.changed_display = True

    def change_name(self):
        """
        The display name has changed, so we might have to issue a DesktopName.

        Thread: Off connection thread
        """
        self.changed_name = True

    def change_frame(self):
        """
        The frame has changed; we may want to update the client.

        Thread: Off connection thread
        """
        self.changed_frame = True

    def change_cursor(self):
        """The cursor has changed and should accompany the next update."""
        self.changed_cursor = True

    def change_clipboard(self):
        """The server clipboard changed and should be sent to this client."""
        self.changed_clipboard = True


class NullLock(object):
    """
    A lock that does nothing.
    """

    def __enter__(self):
        return self

    def __exit__(self, exctype, excvalue, exctb):
        pass


class VNCServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """
    A VNCServer provides the listening socket for a VNC server of a cairo buffer.
    """
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.clients = []
        self.client_lock = threading.Lock()
        self._surface_data = None
        self.options = kwargs.pop('options')
        self.surface = kwargs.pop('surface', None)
        self.surface_lock = kwargs.pop('surface_lock', None) or NullLock()
        self.cursor = kwargs.pop('cursor', None)
        self.clipboard = kwargs.pop('clipboard', None)
        if self.clipboard is not None:
            self.clipboard.encode('iso-8859-1')
        self.surface_data_lock = threading.Lock()

        self.event_queue = queue.Queue(self.options.event_queue_length)

        # Can't do this on Python 2:
        #super(VNCServer, self).__init__(*args, **kwargs)
        socketserver.TCPServer.__init__(self, *args, **kwargs)

    def server_close(self):
        """
        Close the connection to the server

        Thread: Any thread
        """
        # Can't do this on Python 2:
        #super(VNCServer, self).server_close()
        socketserver.TCPServer.server_close(self)

        for client in self.clients:
            # Mark the clients as disconnected so that they close down
            client.disconnect()

        # In order to ensure that clients are not blocked trying to
        # insert data into the event queue, we must also clear it.
        try:
            while True:
                self.event_queue.get_nowait()
        except queue.Empty:
            pass

    def client_connected(self, client):
        """
        Notification that a client has connected and is about to be processed.

        Thread: Connection thread

        @param client:  Client connection object

        @return: True to accept the connection; False to drop it
        """
        if self.options.verbose:
            print("Client connected")
        with self.client_lock:
            if len(self.clients) == self.options.max_clients:
                # There are already the maximum number of clients connected.
                # We're going to drop this connection.
                return False

            self.clients.append(client)
        return True

    def client_disconnected(self, client):
        """
        Notification that a client has disconnected and is about to be closed.

        Thread: Connection thread

        @param client:  Client connection object
        """
        if self.options.verbose:
            print("Client disconnected")
        with self.client_lock:
            self.clients.remove(client)

    def client_log(self, client, message):
        """
        Log messages from a client.

        Thread: Connection thread

        @param client:  Client connection object
        @param message: Message string
        """
        if self.options.verbose:
            print("Client: {}".format(message))

    def surface_data(self):
        """
        Read the current surface data.

        Thread: Connection thread

        @note: Blocks until data is available, which may be delayed by framerate or other
               client's access.

        @return: Tuple of (width, height, data). Data is in the form of a list of rows of bytes
                 in the order BB, GG, RR, xx, ...
        """
        with self.surface_data_lock:
            if not self._surface_data:
                self._surface_data = SurfaceData(self.surface, self.surface_lock,
                                                 max_framerate=self.options.max_framerate)
            return self._surface_data.get_data()

    def surface_size(self):
        """
        Read the current surface width and height.

        Thread: Connection thread

        @return: Tuple of (width, height)
        """
        with self.surface_data_lock:
            if not self._surface_data:
                self._surface_data = SurfaceData(self.surface, self.surface_lock,
                                                 max_framerate=self.options.max_framerate)
            return self._surface_data.get_size()

    def change_surface(self, surface, surface_lock):
        """
        Change the surface which is used by the clients.

        Thread: Off connection thread

        @param surface:         Cairo surface to offer to clients
        @param surface_lock:    threading.Lock() object to use whilst accessing the surface,
                                or None to omit locking.
        """
        surface_lock = surface_lock or NullLock()
        with self.surface_data_lock:
            self.surface_lock = surface_lock

            if self.surface == surface:
                # No change, so don't perform any update and don't invalidate the data
                return

            self.surface = surface
            self._surface_data = None

        # Notify all clients that the surface has changed
        for client in self.clients:
            client.change_surface()

    def change_name(self, name):
        """
        Change the desktop name which is used by the clients.

        Thread: Off connection thread

        @param name:    New name for the display.
        """
        self.options.display_name = name

        # Notify all clients that the name has changed
        for client in self.clients:
            client.change_name()

    def change_frame(self):
        """
        Notify the clients that a new frame is available (for pushing frames)

        Thread: Off connection thread
        """
        for client in self.clients:
            client.change_frame()

    def change_cursor(self, cursor):
        """Change the cursor and notify connected clients."""
        self.cursor = cursor
        for client in self.clients:
            client.change_cursor()

    def change_clipboard(self, text):
        """Change the text clipboard and notify connected clients."""
        # Encoding here validates the value before any client is notified.
        self.clipboard = clipboard_value(text)
        for client in self.clients:
            client.change_clipboard()


class CairoVNCServer(object):
    """
    Public class for CairoVNC servers.

    This class should be the interface that most users will create. It may be
    subclassed to replace functionality (eg replacing the VNCConnection to get
    different information in each client).
    """
    # The class to use for connections (override if you are subclassing)
    connection_class = VNCConnection
    server_class = VNCServer
    event_polling_period = 0.5

    def __init__(self, surface, host='', port=5902, surface_lock=None, options=None, cursor=None,
                 clipboard=None):
        if options is None:
            options = CairoVNCOptions(host=host, port=port)
        self.options = options
        self.surface = surface
        self.surface_lock = surface_lock
        self.cursor = cursor
        self.clipboard = clipboard_value(clipboard) if clipboard is not None else None

        # The object currently available for serving
        self.server = None
        # The thread the server is running on
        self.thread = None

    def start(self):
        """
        Start the server listening for connections.

        Thread: Any thread

        @note: Either serve_forever() or poll() must be called to accept connections.
        """
        if not self.server:
            self.server = self.server_class((self.options.host, self.options.port),
                                             self.connection_class,
                                             surface=self.surface, surface_lock=self.surface_lock,
                                             options=self.options, cursor=self.cursor,
                                             clipboard=self.clipboard)

    def stop(self):
        """
        Stop the server listening and close all client connections.

        Thread: Any thread

        @note: Will block until the server has shut down; connections may however linger for a short period.
        """
        if self.thread:
            # Note: This will block until the server has shut down.
            if self.server:
                self.server.shutdown()
            self.thread = None
        elif self.server:
            self.server.server_close()
            self.server = None

    def serve_forever(self):
        """
        Begin serving on the current thread, until stopped by the stop() method.

        Thread: Any thread

        @note: Blocks until stopped by the stop() method.
        """
        self.start()
        self.server.serve_forever()
        self.server.server_close()
        self.server = None

    def poll(self, timeout=0):
        """
        Poll for any new connections.

        Thread: Any thread

        @param timeout:     None to wait until a new connection received, or a number of seconds to block for
        """
        if not self.server:
            return
        self.server.timeout = timeout
        self.server.handle_request()

    def daemonise(self):
        """
        Start the server listening on a daemon thread (will not block process exit).

        Thread: Any thread

        The server will continue running until it is stopped by the stop() method, or the process exits.
        """
        if self.thread:
            return
        self.thread = threading.Thread(target=self.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def change_surface(self, surface, surface_lock=None):
        """
        Change the surface which is used by the clients.

        Thread: Off connection thread

        @param surface:         Cairo surface to offer to clients
        @param surface_lock:    threading.Lock() object to use whilst accessing the surface,
                                or None to omit locking.
        """
        self.surface = surface
        self.surface_lock = surface_lock
        if self.server:
            self.server.change_surface(surface, surface_lock)

    def change_name(self, name):
        """
        Change the desktop name which is used by the clients.

        Thread: Off connection thread

        @param name:    New name for the display.
        """
        if self.server:
            self.server.change_name(name)

    def change_frame(self):
        """
        Notify the clients that a new frame is available (for pushing frames)

        Thread: Off connection thread
        """
        if self.server:
            self.server.change_frame()

    def change_cursor(self, cursor):
        """Change the cursor which is sent to clients that support it."""
        self.cursor = cursor
        if self.server:
            self.server.change_cursor(cursor)

    def change_cursor_surface(self, surface, hotspot=(0, 0), surface_lock=None):
        """Create a cursor from a Cairo surface and make it current."""
        self.change_cursor(VNCCursor.from_surface(surface, hotspot, surface_lock))

    def change_clipboard(self, text):
        """Change the text clipboard delivered to connected VNC clients."""
        self.clipboard = clipboard_value(text)
        if self.server:
            self.server.change_clipboard(self.clipboard)

    def get_event(self, timeout=None):
        """
        Read an event from the queue, potentially with a timeout.

        Thread: Off connection thread

        @param timeout:     Timeout, in seconds, for reading an event, or None to wait forever

        @return: VNCEvent object (see events.py) or None if no event was pending
        """
        server = self.server
        if not server:
            return None

        if timeout is None:
            # Wait forever (or until the server is stopped) for an event
            event = None
            while self.server:
                try:
                    event = server.event_queue.get(True, self.event_polling_period)
                except queue.Empty:
                    # There was nothing present; so we just keep waiting.
                    pass
            return event

        if timeout <= 0:
            # They just wanted to get a single event, if there was one.
            try:
                event = server.event_queue.get(False)
            except queue.Empty:
                # No event was pending, so return None
                return None

        # They wanted to get an event with a timeout; we need to terminate ourselves
        # if the server is terminated, so we need to do more work here.
        end = time.time() + timeout
        event = None
        while not event and self.server:
            timeout = end - time.time()
            if timeout <= 0:
                break
            try:
                event = server.event_queue.get(True, min(timeout, self.event_polling_period))
            except queue.Empty:
                # No event yet; so keep going.
                pass

        return event
