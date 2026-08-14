"""Fixed-format clipboard values used by the RFB extended clipboard protocol."""


try:
    text_type = unicode
except NameError:
    text_type = str


class VNCClipboard(object):
    """Clipboard data keyed by the RFB extended clipboard format constants."""
    Format_Text = 1 << 0
    Format_RTF = 1 << 1
    Format_HTML = 1 << 2
    Formats = (Format_Text, Format_RTF, Format_HTML)

    def __init__(self, formats=None):
        self.formats = {}
        if formats:
            for clipboard_format, value in formats.items():
                self.set(clipboard_format, value)

    def set(self, clipboard_format, value):
        if clipboard_format not in self.Formats:
            raise ValueError('Unsupported clipboard format {}'.format(clipboard_format))
        if clipboard_format == self.Format_Text:
            if not isinstance(value, text_type):
                value = _as_bytes(value).decode('utf-8')
            self.formats[clipboard_format] = value.replace(u'\r\n', u'\n')
        else:
            self.formats[clipboard_format] = _as_bytes(value)

    @property
    def text(self):
        return self.formats.get(self.Format_Text)

    def format_flags(self):
        flags = 0
        for clipboard_format in self.formats:
            flags |= clipboard_format
        return flags

    def wire_data(self, clipboard_format):
        value = self.formats[clipboard_format]
        if clipboard_format == self.Format_Text:
            return value.replace(u'\n', u'\r\n').encode('utf-8') + b'\0'
        return value


def _as_bytes(value):
    if isinstance(value, bytearray):
        if hasattr(value, 'tobytes'):
            return value.tobytes()
        return value.tostring()
    if isinstance(value, text_type):
        raise TypeError('Clipboard binary data must be bytes')
    return b''.join([value])
