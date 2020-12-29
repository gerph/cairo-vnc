# Testing the cairo system

import cairo

class Screen(object):
    def __init__(self):
        self.width = 200
        self.height = 200

        self.surface = cairo.ImageSurface(cairo.Format.ARGB32, self.width, self.height)
        self.context = cairo.Context(self.surface)
        self.context.set_source_rgb(0.5, 0.5, 0.5)
        self.context.rectangle(0, 0, self.width, self.height)
        self.context.fill()

        self.context.set_source_rgb(1, 1, 1)

        x, y, x1, y1 = 0.1, 0.5, 0.4, 0.9
        x2, y2, x3, y3 = 0.6, 0.1, 0.9, 0.5
        self.context.scale(200, 200)
        self.context.set_line_width(0.04)
        self.context.move_to(x, y)
        self.context.curve_to(x1, y1, x2, y2, x3, y3)
        self.context.stroke()

        self.context.set_source_rgba(1, 0.2, 0.2, 0.6)
        self.context.set_line_width(0.02)
        self.context.move_to(x, y)
        self.context.line_to(x1, y1)
        self.context.move_to(x2, y2)
        self.context.line_to(x3, y3)
        self.context.stroke()
        self.surface.write_to_png('image.png')

screen = Screen()


import array
import fcntl
import select
import struct
import socketserver
import termios
import time


class VNCConstants(object):

    Security_Invalid = 0
    Security_None = 1
    Security_VNCAuth = 2

    SecurityResult_OK = 0
    SecurityResult_Failed = 1

    ClientInit_Exclusive = 0
    # All other values are shared access

    PixelFormat_BigEndian = 1
    PixelFormat_LittleEndian = 0
    PixelFormat_TrueColour = 1
    PixelFormat_Paletted = 0

    ClientMsgType_SetPixelFormat = 0
    ClientMsgType_SetEncodings = 2
    ClientMsgType_FramebufferUpdateRequest = 3
    ClientMsgType_KeyEvent = 4
    ClientMsgType_PointerEvent = 5
    ClientMsgType_ClientCutText = 6

    ServerMsgType_FramebufferUpdate = 0
    ServerMsgType_SetColourMapEntries = 1
    ServerMsgType_Bell = 2
    ServerMsgType_ServerCutText = 3


class PixelFormat(object):
    # Default parameters
    bpp = 32
    depth = 24
    endianness = VNCConstants.PixelFormat_LittleEndian
    truecolour = VNCConstants.PixelFormat_TrueColour
    redmax = 255
    greenmax = 255
    bluemax = 255
    redshift = 0
    greenshift = 8
    blueshift = 16
    padding = '\x00\x00\x00'

    def __init__(self, data=None):
        """
        Pixel Format data structure representation.

        See 7.4 Pixel Format Data Structure.
        """
        if data:
            self.decode(data)

    def decode(self, data):
        (self.bpp, self.depth, bigendian, truecolour,
         self.redmax, self.greenmax, self.bluemax,
         self.redshift, self.greenshift, self.blueshift,
         _) = struct.unpack('>HHBBBBHHHBBB3sL', data)
        self.truecolour = VNCConstants.PixelFormat_TrueColour if truecolour else VNCConstants.PixelFormat_Paletted
        self.endianness = VNCConstants.PixelFormat_BigEndian if bigendian else VNCConstants.PixelFormat_LittleEndian

    def encode(self):
        data = struct.pack('>BBBBHHHBBB3s',
                           self.bpp, self.depth, self.endianness, self.truecolour,
                           self.redmax, self.greenmax, self.bluemax,
                           self.redshift, self.greenshift, self.blueshift,
                           '\x00\x00\x00')
        return data


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
        print("Comm: {}".format(message))

    def readdata(self, nbytes):
        """
        Read data from the socket - may be overridden to decrypt the data from the wire
        """
        return self.sock.recv(nbytes)

    def writedata(self, data):
        """
        Write data to the socket - may be overridden to encrypt the data on the wire
        """
        self.log("Sending %r" % (data,))
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
                    data = first[:size]
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


message_handlers = {}


def register_msg(msgtype, payload_size):
    def register_func(func):
        message_handlers[msgtype] = (func, payload_size)
        return func
    return register_func


@register_msg(VNCConstants.ClientMsgType_SetPixelFormat, payload_size=3 + 16)
def msg_setpixelformat(server, payload):
    server.log("SetPixelFormat: %r" % (payload,))
    server.pixelformat.decode(payload)


@register_msg(VNCConstants.ClientMsgType_SetEncodings, payload_size=1 + 2)
def msg_SetEncodings(server, payload):
    (_, nencodings) = struct.unpack('>BH', payload)
    response = server.read(4 * nencodings, timeout=server.client_timeout)
    if not response:
        server.log("Timeout reading SetEncodings data")
        return
    encodings = struct.unpack('>' + 'l' * nencodings, response)
    server.log("SetEncodings: %i encodings: (%r)" % (nencodings, encodings))


@register_msg(VNCConstants.ClientMsgType_FramebufferUpdateRequest, payload_size=1 + 2 * 4)
def msg_FramebufferUpdateRequest(server, payload):
    (incremental, xpos, ypos, width, height) = struct.unpack('>BHHHH', payload)
    server.log("FramebufferUpdateRequest: incremental=%i, pos=%i,%i, size=%i,%i" % (incremental,
                                                                               xpos, ypos,
                                                                               width, height))


@register_msg(VNCConstants.ClientMsgType_KeyEvent, payload_size=1 + 2 + 4)
def msg_KeyEvent(server, payload):
    (down, _, key) = struct.unpack('>BHL', payload)
    server.log("KeyEvent: key=%i, down=%i" % (key, down))


@register_msg(VNCConstants.ClientMsgType_PointerEvent, payload_size=1 + 2 * 2)
def msg_PointerEvent(server, payload):
    (buttons, xpos, ypos) = struct.unpack('>BHH', payload)
    server.log("PointerEvent: buttons=%i, pos=%i,%i" % (buttons, xpos, ypos))


@register_msg(VNCConstants.ClientMsgType_ClientCutText, payload_size=3 + 4)
def msg_ClientCutText(server, payload):
    (_, textlen) = struct.unpack('>3sL', payload)
    response = server.read(textlen, timeout=server.client_timeout)
    if not response:
        server.log("Timeout reading ClientCutText data (2)")
        return
    text = response.decode('iso-8859-1')
    server.log("ClientCutText: textlen=%i, text=%r" % (textlen, text))


class VNCServerInstance(socketserver.BaseRequestHandler):
    """
    A VNCServerInstance handles communication with one client.
    """
    connect_timeout = 10
    client_timeout = 10
    security_supported = [
        VNCConstants.Security_None
    ]

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
        self.pixelformat.redshift = 0
        self.pixelformat.greenshift = 8
        self.pixelformat.blueshift = 16

    def read(self, size, timeout):
        return self.stream.read_nbytes(size, timeout=timeout)

    def write(self, data):
        return self.stream.writedata(data)

    def log(self, message):
        self.server.client_log(self, message)

    def handle(self):
        self.log("Request received")
        self.log("server: %r" % (self.server,))
        self.server.client_connected(self)

        # 7.1.1 ProtocolVersion Handshake
        self.stream.writedata('RFB 003.008\n')

        protocol_handshake = self.stream.read_upto(terminator='\n', timeout=self.connect_timeout)
        if not protocol_handshake:
            # FIXME: Report failed connection?
            return

        self.log("Protocol handshake: {!r}".format(protocol_handshake))
        if not protocol_handshake.startswith('RFB 003'):
            self.log("Don't understand the protocol. Giving up.")
            # FIXME: Report the failure
            return
        protocol = protocol_handshake[4:]

        # 7.1.2. Security handshake
        if protocol >= '003.007':
            security_data = [len(self.security_supported)]
            security_data.extend(self.security_supported)
            data = bytearray(security_data)
            self.stream.writedata(data)

            response = self.read(1, timeout=self.connect_timeout)
            if not response:
                # Timeout, or disconnect
                self.log("Timed out at Security Handshake")
                # FIXME: Report the failure
                return
            security_requested = bytearray(response)[0]
        else:
            data = struct.pack('>I', VNCConstants.Security_None)
            self.stream.writedata(data)
            security_requested = VNCConstants.Security_None

        if security_requested != VNCConstants.Security_None:
            self.log("Invalid security type: {}".format(security_requested))
            # FIXME: Report the failure
            return
        # FIXME: Abstract security handling to give us a way to extend here.

        # For 'No encryption' there isn't a SecurityResult prior to 3.8
        has_security_result = (protocol >= '003.008')

        if has_security_result:
            # 7.1.3. SecurityResult Handshake
            data = struct.pack('>L', VNCConstants.SecurityResult_OK)
            self.log("Security result: %r" % (data,))
            self.stream.writedata(data)

        # 7.3.1. ClientInit
        response = self.read(1, timeout=self.connect_timeout)
        if not response:
            # Timeout, or disconnect
            self.log("Timed out at ClientInit")
            # FIXME: Report the failure
            return

        (shared_flag,) = struct.unpack('B', response)
        # FIXME: Do we want to honour this or just ignore it?
        # FIXME: Maybe report it?

        # 7.3.2. ServerInit
        width = 640
        height = 480
        name = 'cairo'

        data_size = struct.pack('>HH', width, height)
        data_pixelformat = self.pixelformat.encode()
        data_name = struct.pack('>L', len(name)) + name
        self.log("ServerInit message: %r" % (data,))
        self.stream.writedata(data_size + data_pixelformat + data_name)

        # Now we read messages from the client
        while not self.stream.closed:
            response = self.read(1, timeout=self.client_timeout)
            if response:
                msgtype = bytearray(response)[0]
                if msgtype in message_handlers:
                    (func, payload_size) = message_handlers[msgtype]
                    name = func.__name__
                    response = self.read(payload_size, timeout=self.client_timeout)
                    if not response:
                        self.log("Timeout reading payload data for {}".format(name))
                        break
                    func(self, response)

                else:
                    self.log("Unrecognised message type : %i" % (msgtype,))
                    break

    def finish(self):
        self.server.client_disconnected(self)


class VNCServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """
    A VNCServer provides the listening socket for a VNC server of a cairo buffer.
    """
    allow_reuse_address = True

    clients = []

    def client_connected(self, client):
        print("Client connected")
        self.clients.append(client)

    def client_disconnected(self, client):
        print("Client disconnected")
        self.clients.remove(client)

    def client_log(self, client, message):
        print("Client: {}".format(message))


'''
class VNCServer(object):
    """
    A VNCServer providing a simple interface to a Cairo surface.
    """
    def __init__(self, port):
'''


if __name__ == "__main__":
    (HOST, PORT) = ("localhost", 5902)

    # Create the server
    server = VNCServer((HOST, PORT), VNCServerInstance)

    # Activate the server; this will keep running until you
    # interrupt the program with Ctrl-C
    server.serve_forever()
