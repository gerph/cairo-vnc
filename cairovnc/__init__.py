"""
Cairo surface served over VNC.

Usage:

    server = cairovnc.CairoVNCServer(surface=surface, port=5900)
    server.serve_forever()
"""

import array
import fcntl
import select
import struct
try:
    import socketserver
except ImportError:
    # Python 2 compatibility.
    import SocketServer as socketserver
import termios
import time

from .constants import VNCConstants
from .surfacedata import SurfaceData
from .pixeldata import PixelFormat
from .clientmsg import dispatch_msg
from .regions import Regions, RegionRequest
from .security import get_security_types


class CairoVNCOptions(object):
    """
    A container object holding the options that can be set on a server and connection.
    """

    def __init__(self, host='0.0.0.0', port=5900, password=None):
        self.host = host
        self.port = port

        # Set password to None to allow any connections (although the macOS screen sharing
        # hangs if you do this).
        self.password = password

        # The maximum speed at which we will deliver frame updates, regardless of what the
        # clients request.
        self.max_framerate = 10

    def copy(self):
        obj = CairoVNCOptions(host=self.host,
                              port=self.port,
                              password=self.password)
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
        return self.sock.recv(nbytes)

    def writedata(self, data):
        """
        Write data to the socket - may be overridden to encrypt the data on the wire
        """
        #self.log("Sending %r" % (data,))
        self.sock.send(data)

    def fionread(self):
        if fcntl.ioctl(self.sock, termios.FIONREAD, self.fionread_data) == -1:
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

        while True:
            timeout = endtime - time.time()
            if timeout <= 0:
                break
            (rlist, wlist, xlist) = select.select([self.sock], [], [], timeout)
            if rlist:
                nbytes = self.fionread()
                if nbytes == 0:
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
        while True:
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
                if nbytes == 0:
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

        # We copy the options because they might be changed by security or other interaction.
        self.options = self.server.options.copy()

        # Changes that are pending
        self.changed_display = False
        self.changed_name = False

        # The capabilities for communicating with the client
        self.capabilities = set([])
        self.request_regions = Regions()
        self.last_rows = {}

    def finish(self):
        self.server.client_disconnected(self)
        self.stream.closed = True

    def disconnect(self):
        """
        Request to disconnect this client.
        """
        # We flag this by treating the stream as closed, so that we exit our handling loop
        self.stream.closed = True

    def read(self, size, timeout):
        return self.stream.read_nbytes(size, timeout=timeout)

    def write(self, data):
        return self.stream.writedata(data)

    def log(self, message):
        self.server.client_log(self, message)

    def do_protocol(self):
        """
        7.1.1 ProtocolVersion Handshake

        Announce ourselves, and find out what protocol they want to speak.

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

        @return: True if we were successful; False if something went wrong.
        """
        (width, height, rows) = self.server.surface_data()
        self.width = width
        self.height = height
        name = self.server.display_name

        data_size = struct.pack('>HH', width, height)
        data_pixelformat = self.pixelformat.encode()
        name_latin1 = name.encode('iso-8859-1')
        data_name = struct.pack('>L', len(name_latin1)) + name_latin1
        data = data_size + data_pixelformat + data_name
        self.log("ServerInit message: %r" % (data,))
        self.stream.writedata(data)

        return True

    def handle(self):
        self.log("Connection received")
        self.server.client_connected(self)

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
            response = self.read(1, timeout=self.client_timeout)
            if response:
                msgtype = bytearray(response)[0]
                handled = dispatch_msg(msgtype, self)
                if not handled:
                    # Something went wrong; so we're done with this connection
                    break

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

            # If they requested some region to be drawn, so we should dispatch
            # a frame buffer update
            while self.request_regions:
                region = self.request_regions.pop()
                self.update_framebuffer(region)

    def update_framebuffer(self, region):
        """
        Framebuffer updates here use only whole rows, because we're lazy here.
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

    def change_surface(self):
        """
        The display surface has changed, so we might need to issue a DesktopSize.
        """
        self.changed_display = True


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
        self._surface_data = None
        self._display_name = None
        self.options = kwargs.pop('options')
        self.display_name = kwargs.pop('display_name', 'cairo')
        self.surface = kwargs.pop('surface', None)
        self.surface_lock = kwargs.pop('surface_lock', None)
        # Can't do this on Python 2:
        #super(VNCServer, self).__init__(*args, **kwargs)
        socketserver.TCPServer.__init__(self, *args, **kwargs)

    def server_close(self):
        # Can't do this on Python 2:
        #super(VNCServer, self).server_close()
        socketserver.TCPServer.server_close()

        for client in self.clients:
            # Mark the clients as disconnected so that they close down
            client.disconnect()

    def client_connected(self, client):
        print("Client connected")
        self.clients.append(client)

    def client_disconnected(self, client):
        print("Client disconnected")
        self.clients.remove(client)

    def client_log(self, client, message):
        print("Client: {}".format(message))

    def surface_data(self):
        if not self._surface_data:
            print("Updating surface_data")
            self._surface_data = SurfaceData(self.surface, max_framerate=self.options.max_framerate)
        return self._surface_data.get_data()

    def surface_size(self):
        if not self._surface_data:
            print("Updating surface_data")
            self._surface_data = SurfaceData(self.surface, max_framerate=self.options.max_framerate)
        return self._surface_data.get_size()

    def change_surface(self, surface, surface_lock):
        print("change_surface")
        self.surface_lock = surface_lock or NullLock()

        if self.surface == surface:
            # No change, so don't perform any update and don't invalidate the data
            return

        self.surface = surface
        self._surface_data = None

        # Notify all clients that the display has changed
        print("Surface changing ({} clients)".format(len(self.clients)))
        for client in self.clients:
            client.change_surface()

    @property
    def display_name(self):
        return self._display_name

    @display_name.setter
    def display_name(self, value):
        self._display_name = value

        # Notify all clients that the display name has changed
        for client in self.clients:
            client.name_change()


class CairoVNCServer(object):
    # The class to use for connections (override if you are subclassing)
    connection_class = VNCConnection

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
        if not self.server:
            self.server = VNCServer((self.options.host, self.options.port),
                                    self.connection_class,
                                    surface=self.surface, options=self.options)

    def stop(self):
        if self.thread:
            # Note: This will block until the server has shut down.
            self.shutdown()
            self.thread = None
        elif self.server:
            self.server.server_close()
            self.server = None

    def serve_forever(self):
        self.start()
        self.server.serve_forever()
        self.server.server_close()
        self.serevr = None

    def daemonise(self):
        self.thread = threading.Thread(target=self.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def change_surface(self, surface, surface_lock=None):
        if self.server:
            self.surface = surface
            self.surface_lock = surface_lock
            self.server.change_surface(surface, surface_lock)
