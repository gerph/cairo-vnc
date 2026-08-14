"""Show vertical and horizontal pointer-wheel events."""

import cairo
import threading

import cairovnc


WIDTH = 300
HEIGHT = 200
surface_lock = threading.Lock()
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
offset_x = 0
offset_y = 0


def draw():
    context = cairo.Context(surface)
    context.set_source_rgb(0.15, 0.15, 0.15)
    context.paint()
    context.set_source_rgb(0.2, 0.7, 1.0)
    context.rectangle(120 + offset_x, 70 + offset_y, 60, 60)
    context.fill()
    context.set_source_rgb(1, 1, 1)
    context.move_to(10, 20)
    context.show_text('Use both pointer wheels to move the square')


draw()
options = cairovnc.CairoVNCOptions(port=5902)
options.read_only = False
server = cairovnc.CairoVNCServer(surface, surface_lock=surface_lock, options=options)
server.daemonise()

while True:
    event = server.get_event()
    if event and event.name == 'scroll':
        offset_x += event.dx * 10
        offset_y -= event.dy * 10
        with surface_lock:
            draw()
        server.change_frame()
