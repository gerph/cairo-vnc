"""
Constants from the VNC protocol.

See RFC6143, https://github.com/rfbproto/rfbproto/blob/master/rfbproto.rst.
"""

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

    # Client message types (required)
    ClientMsgType_SetPixelFormat = 0
    ClientMsgType_SetEncodings = 2
    ClientMsgType_FramebufferUpdateRequest = 3
    ClientMsgType_KeyEvent = 4
    ClientMsgType_PointerEvent = 5
    ClientMsgType_ClientCutText = 6
    # Client message types (extensions)
    ClientMsgType_ResizeFrameBuffer = 4
    ClientMsgType_KeyFrameUpdate = 5
    ClientMsgType_FileTransfer = 7
    ClientMsgType_TextChat = 11
    ClientMsgType_KeepAlive = 13
    ClientMsgType_ResizeFrameBuffer = 15
    ClientMsgType_VMware = 127
    ClientMsgType_CarConnectivity = 128
    ClientMsgType_EndOfContinuousUpdates = 150
    ClientMsgType_ServerState = 173
    ClientMsgType_ServerFence = 248
    ClientMsgType_OLIVECallControl = 249
    ClientMsgType_xvpServerMessage = 250
    ClientMsgType_tight = 252
    ClientMsgType_giiServerMessage = 253
    ClientMsgType_VMware = 254
    ClientMsgType_QEMUServerMessage = 255

    # Server message types (required)
    ServerMsgType_FramebufferUpdate = 0
    ServerMsgType_SetColourMapEntries = 1
    ServerMsgType_Bell = 2
    ServerMsgType_ServerCutText = 3
    # Server message types (extensions)
    ServerMsgType_ResizeFrameBuffer = 4
    ServerMsgType_KeyFrameUpdate = 5
    ServerMsgType_FileTransfer = 7
    ServerMsgType_TextChat = 11
    ServerMsgType_KeepAlive = 13
    ServerMsgType_ResizeFrameBuffer = 15
    ServerMsgType_VMware = 127
    ServerMsgType_CarConnectivity = 128
    ServerMsgType_EndOfContinuousUpdates = 150
    ServerMsgType_ServerState = 173
    ServerMsgType_ServerFence = 248
    ServerMsgType_OLIVECallControl = 249
    ServerMsgType_xvpServerMessage = 250
    ServerMsgType_tight = 252
    ServerMsgType_giiServerMessage = 253
    ServerMsgType_VMware = 254
    ServerMsgType_QEMUServerMessage = 255

    # Framebuffer encodings
    Encoding_Raw = 0
    Encoding_CopyRect = 1
    Encoding_RRE = 2
    Encoding_CoRRE = 4
    Encoding_Hextile = 5
    Encoding_zlib = 6
    Encoding_Tight = 7
    Encoding_zlibhex = 8
    Encoding_ZRLE = 16
    Encoding_TightPNG = -260

    # Capabilities (as encodings)
    PseudoEncoding_JPEGQualityBase = -23
    PseudoEncoding_DesktopSize = -223
    PseudoEncoding_LastRect = -224
    PseudoEncoding_Cursor = -239
    PseudoEncoding_XCursor = -240
    PseudoEncoding_CompressionLevel = -247
    PseudoEncoding_QEMUPointerMotionChange = -257
    PseudoEncoding_QEMUExtendedKeyEvent = -258
    PseudoEncoding_QEMUAudio = -259
    PseudoEncoding_QEMULEDState = -261
    PseudoEncoding_gii = -305
    PseudoEncoding_DesktopName = -307
    PseudoEncoding_ExtendedDesktopSize = -308
    PseudoEncoding_xvp = -309
    PseudoEncoding_Fence = -312
    PseudoEncoding_ContinuousUpdates = -313
    PseudoEncoding_CursorWithAlpha = -314
    PseudoEncoding_JPEGFineGrainedQualityLevel = -412
    PseudoEncoding_JPEGSubsamplingLevel = -763
    PseudoEncoding_VMwareCursor = 0x574d5664
    PseudoEncoding_VMwareCursorState = 0x574d5665
    PseudoEncoding_VMwareCursorPosition = 0x574d5666
    PseudoEncoding_VMwareKeyRepeat = 0x574d5667
    PseudoEncoding_VMwareLEDState = 0x574d5668
    PseudoEncoding_VMwareDisplayModeChange = 0x574d5669
    PseudoEncoding_VMwareVirtualMachineState = 0x574d566a
    PseudoEncoding_ExtendedClipboard = 0xc0a1e5ce

    encoding_names = {}


VNCConstants.encoding_names = dict((getattr(VNCConstants, name), name) for name in dir(VNCConstants) if name.startswith(('Encoding', 'PseudoEncoding')))
