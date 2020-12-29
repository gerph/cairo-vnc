"""
Test that the VNC server works.

Run a simple animation in the Cairo surface, on a thread.
Then run a server on localhost:5902 / localhost:2 which should display the animation.
"""

import math
import threading
import time

import cairo

import cairovnc

class Screen(object):
    def __init__(self):
        self.width = 200
        self.height = 200
        self.seq = 0

        self.surface = cairo.ImageSurface(cairo.Format.ARGB32, self.width, self.height)
        self.context = cairo.Context(self.surface)
        self.draw()

    def draw(self):
        self.context.set_source_rgb(0.5, 0.5, 0.5)
        self.context.rectangle(0, 0, self.width, self.height)
        self.context.fill()

        self.context.set_source_rgb(1, 1, 1)

        delta = math.cos(self.seq * math.pi / 10)

        x, y, x1, y1 = 0.1, 0.5, 0.4, 0.5 + delta * 0.4
        x2, y2, x3, y3 = 0.6, 0.1, 0.9, 0.5
        self.context.save()
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
            time.sleep(0.1)
            self.draw()
            self.seq += 1


screen = Screen()
animate_thread = threading.Thread(target=screen.animate)
animate_thread.daemon = True
animate_thread.start()


if __name__ == "__main__":
    (HOST, PORT) = ("localhost", 5902)

    # Create the server
    server = cairovnc.VNCServer((HOST, PORT), cairovnc.VNCServerInstance, surface=screen.surface)

    # Activate the server; this will keep running until you
    # interrupt the program with Ctrl-C
    server.serve_forever()
