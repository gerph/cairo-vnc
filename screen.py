# Testing the cairo system

import cairo

import cairovnc

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

        self.context.set_source_rgb(1, 0, 0)
        self.context.rectangle(0.1, 0, 0.1, 0.1)
        self.context.fill()

        self.context.set_source_rgb(0, 1, 0)
        self.context.rectangle(0.3, 0, 0.1, 0.1)
        self.context.fill()

        self.context.set_source_rgb(0, 0, 1)
        self.context.rectangle(0.5, 0, 0.1, 0.1)
        self.context.fill()

        self.surface.write_to_png('image.png')

screen = Screen()


if __name__ == "__main__":
    (HOST, PORT) = ("localhost", 5902)

    # Create the server
    server = cairovnc.VNCServer((HOST, PORT), cairovnc.VNCServerInstance, surface=screen.surface)

    # Activate the server; this will keep running until you
    # interrupt the program with Ctrl-C
    server.serve_forever()
