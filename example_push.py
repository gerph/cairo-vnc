"""
Show we can read input events from the connected clients.

Run a simple animation in the Cairo surface, on a thread.
Then run a server on localhost:5902 / localhost:2 which should display the animation.

Instead of just sleeping during the animation delay, we now pull events from the
VNC server. These events are then used to control the shapes in the animation.
The pointer position controls one of the bezier control points.
The mouse clicks control cycling of colours of the squares (for the first 3
buttons).
"""

import math
import threading
import time

import cairo

import cairovnc


class Screen(object):
    surface_change_func = None
    animation_period = 0.1
    colour_cycling = [
            (1, 0, 0),
            (0, 1, 0),
            (0, 0, 1),
            (1, 1, 0),
            (0, 1, 1),
            (1, 0, 1)
        ]

    def __init__(self):
        self.surface_lock = threading.Lock()
        self.width = 200
        self.height = 200
        self.square_indexes = [0, 1, 2]
        self.ctrlx = self.width / 2
        self.ctrly = self.height / 2
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
        x2, y2, x3, y3 = float(self.ctrlx) / self.width, float(self.ctrly) / self.height, 0.9, 0.5
        self.context.save()
        self.context.scale(self.width, self.height)

        # Bezier curve
        self.context.set_line_width(0.04)
        self.context.move_to(x, y)
        self.context.curve_to(x1, y1, x2, y2, x3, y3)
        self.context.stroke()

        # Control points
        self.context.set_source_rgba(1, 0.2, 0.2, 0.6)
        self.context.set_line_width(0.02)
        self.context.move_to(x, y)
        self.context.line_to(x1, y1)
        self.context.move_to(x2, y2)
        self.context.line_to(x3, y3)
        self.context.stroke()

        # Red square (which can cycle colours)
        colour = self.colour_cycling[self.square_indexes[0]]
        self.context.set_source_rgb(*colour)
        self.context.rectangle(0.1, 0 + delta * 0.05, 0.1, 0.1)
        self.context.fill()

        # Green square (which can cycle colours)
        colour = self.colour_cycling[self.square_indexes[1]]
        self.context.set_source_rgb(*colour)
        self.context.rectangle(0.3, 0, 0.1, 0.1)
        self.context.fill()

        # Blue square (which can cycle colours)
        colour = self.colour_cycling[self.square_indexes[2]]
        self.context.set_source_rgb(*colour)
        self.context.rectangle(0.5, 0, 0.1, 0.1)
        self.context.fill()
        self.context.restore()

        #self.surface.write_to_png('image.png')

    def get_event(self, timeout=None):
        if timeout:
            time.sleep(timeout)
        return None

    def frame_complete(self):
        pass

    def animate(self):
        while True:
            self.seq += 1
            # Read events for up to our time slice
            end = time.time() + self.animation_period
            while time.time() < end:
                event = self.get_event(timeout=end - time.time())
                if event:
                    if event.name == 'move':
                        self.ctrlx = event.x
                        self.ctrly = event.y
                    if event.name == 'click' and event.down:
                        if event.button < 3:
                            # When they click a button, advance the colours of the squares
                            self.square_indexes[event.button] = (self.square_indexes[event.button] + 1) % len(self.colour_cycling)

            # All accesses to the surface are protected by a lock, so that the clients see
            # them in one go.
            with self.surface_lock:
                self.draw()
                self.frame_complete()


screen = Screen()
animate_thread = threading.Thread(target=screen.animate)
animate_thread.daemon = True
animate_thread.start()


if __name__ == "__main__":
    # Create the server with options
    options = cairovnc.CairoVNCOptions(port=5902)
    options.read_only = False
    options.verbose = True
    options.password = 'pass'
    server = cairovnc.CairoVNCServer(surface=screen.surface, surface_lock=screen.surface_lock,
                                     options=options)
    screen.get_event = server.get_event
    screen.frame_complete = server.change_frame

    # We require a change function to update the clients with the new size
    screen.surface_change_func = server.change_surface
    server.serve_forever()
