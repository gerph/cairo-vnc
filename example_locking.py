"""
Show how to lock access so that partial frames are not delivered.

Run a simple animation in the Cairo surface, on a thread.
Then run a server on localhost:5902 / localhost:2 which should display the animation.

Here we use a thread to lock access to the surface whilst it's being updated.
These locks ensure that the threads which are supplying data to the VNC clients
do not attempt to access the surface data whilst a frame is being constructed.
This would have undefined behaviour for Cairo, with the best result being that partial
frames would be delivered to the client.
"""

import math
import threading
import time

import cairo

import cairovnc


class Screen(object):
    surface_change_func = None

    def __init__(self):
        self.surface_lock = threading.Lock()
        self.width = 200
        self.height = 200
        self.seq = 0
        self.setup_surface()

    def setup_surface(self):
        self.surface = cairo.ImageSurface(cairo.Format.ARGB32, self.width, self.height)
        self.context = cairo.Context(self.surface)
        if self.surface_change_func:
            self.surface_change_func(self.surface, surface_lock=self.surface_lock)

    def draw(self):
        self.context.set_source_rgb(0.5, 0.5, 0.5)
        self.context.rectangle(0, 0, self.width, self.height)
        self.context.fill()

        self.context.set_source_rgb(1, 1, 1)

        delta = math.cos(self.seq * math.pi / 10)

        x, y, x1, y1 = 0.1, 0.5, 0.4, 0.5 + delta * 0.4
        x2, y2, x3, y3 = 0.6, 0.1, 0.9, 0.5
        self.context.save()
        self.context.scale(self.width, self.height)
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

        self.context.set_source_rgb(1, 0, 0)
        self.context.rectangle(0.1, 0 + delta * 0.05, 0.1, 0.1)
        self.context.fill()

        self.context.set_source_rgb(0, 1, 0)
        self.context.rectangle(0.3, 0, 0.1, 0.1)
        self.context.fill()

        self.context.set_source_rgb(0, 0, 1)
        self.context.rectangle(0.5, 0, 0.1, 0.1)
        self.context.fill()
        self.context.restore()

        #self.surface.write_to_png('image.png')

    def animate(self):
        while True:
            self.seq += 1
            time.sleep(0.1)

            # All accesses to the surface are protected by a lock, so that the clients see
            # them in one go.
            with self.surface_lock:
                # Once every 20 calls we resize the surface
                if self.seq % 20 == 0:
                    if int(self.seq / 20) % 2:
                        self.width = self.width + 20
                    else:
                        self.width = self.width - 20
                    self.setup_surface()

                self.draw()


screen = Screen()
animate_thread = threading.Thread(target=screen.animate)
animate_thread.daemon = True
animate_thread.start()


if __name__ == "__main__":
    # Create the server with options
    options = cairovnc.CairoVNCOptions(port=5902)
    server = cairovnc.CairoVNCServer(surface=screen.surface, surface_lock=screen.surface_lock,
                                     options=options)

    # We require a change function to update the clients with the new size
    screen.surface_change_func = server.change_surface
    server.serve_forever()
