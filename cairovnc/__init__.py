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

from .constants import VNCConstants
from .surfacedata import SurfaceData
from .pixeldata import PixelFormat
from .clientmsg import dispatch_msg
from .regions import Regions, RegionRequest
from .security import get_security_types


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
        self.pointer_xpos = -1
        self.pointer_ypos = -1

        # We copy the options because they might be changed by security or other interaction.
        self.options = self.server.options.copy()

        # Changes that are pending
        self.changed_display = False
        self.changed_name = False

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

        nrects = len(redraw_range)
        msg_data = [struct.pack('>BBH', VNCConstants.ServerMsgType_FramebufferUpdate,
                                        0,
                                        nrects)]
        if nrects:
            self.log("FramebufferUpdate: {} rectangles to send".format(nrects))
            for y0, rows in redraw_range:

                rows_data = [struct.pack('>HHHHl', 0, y0, width, rows, VNCConstants.Encoding_Raw)]
                self.log("    Sending rows {} - {}".format(y0, y0 + rows))
                for y in range(y0, y0 + rows):
                    rows_data.append(self.pixelformat.converter(surface_rows[y]))
                    self.last_rows[y] = surface_rows[y]

                msg_data.extend(rows_data)

        msg = b''.join(msg_data)
        self.write(msg)

    def set_capabilities(self, capabilities):
        """
        Update the capabilities used by this client.

        Thread: Connection thread

        @param capabilities: A list of the encodings that the client is capable of
        """
        self.capabilities |= set(capabilities)
        if VNCConstants.PseudoEncoding_Apple1011 in capabilities:
            # This is an Apple Screen Sharing client.
            # So we're going to enable the push frames, as otherwise it won't update.
            self.options.push_requests = True

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
        self.surface_lock = kwargs.pop('surface_lock', NullLock())
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

    def __init__(self, surface, host='', port=5902, surface_lock=None, options=None):
        if options is None:
            options = CairoVNCOptions(host=host, port=port)
        self.options = options
        self.surface = surface
        self.surface_lock = None

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
                                             surface=self.surface, options=self.options)

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
