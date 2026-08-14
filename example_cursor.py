"""Serve a Cairo-drawn cursor which changes shape every second."""

import cairo
import time

import cairovnc


surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 300, 200)
context = cairo.Context(surface)
context.set_source_rgb(0.2, 0.3, 0.5)
context.paint()

cursor_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 24, 24)
options = cairovnc.CairoVNCOptions(port=5902)
server = cairovnc.CairoVNCServer(surface, options=options)
server.daemonise()

while True:
    cursor_context = cairo.Context(cursor_surface)
    cursor_context.set_operator(cairo.OPERATOR_CLEAR)
    cursor_context.paint()
    cursor_context.set_operator(cairo.OPERATOR_OVER)
    cursor_context.set_source_rgba(1, 1, 1, 1)
    cursor_context.arc(12, 12, 8, 0, 2 * 3.14159)
    cursor_context.fill()
    server.change_cursor_surface(cursor_surface, hotspot=(12, 12))
    time.sleep(1)
