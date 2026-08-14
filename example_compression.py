"""Serve a highly compressible frame; enable verbose output to inspect zlib sizes."""

import cairo
import cairovnc


surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 800, 600)
context = cairo.Context(surface)
context.set_source_rgb(0.1, 0.25, 0.5)
context.paint()
context.set_source_rgb(1, 1, 1)
for y in range(20, 600, 40):
    context.move_to(20, y)
    context.show_text('Repeated content makes zlib transfer sizes easy to compare.')

options = cairovnc.CairoVNCOptions(port=5902)
options.verbose = True
server = cairovnc.CairoVNCServer(surface, options=options)
server.serve_forever()
