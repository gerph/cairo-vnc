# CairoVNC - VNC server for Cairo surfaces

## Introduction

This repository holds an implementation of a VNC server that can supply the contents of a
PyCairo surface to multiple clients.

Currently a work in progress, it is intended to incorporate most of the simple features of
VNC screen and the keyboard and mouse input.

## Usage

The code for the VNC server is all in the `cairovnc` directory. It'll be at varying degrees
of completeness as time goes on.

No test suites as yet.

To test it by hand:

    python example_animation.py

then connect to VNC display 2 (sometimes listed as port 5902, rather than display 2) on
localhost.

## Examples

In all the examples they are listening on VNC display 2 (port 5902):


### Basic usage

The basic usage of the VNC server is that you have a static Cairo surface and you draw to
it in on one thread, and on another thread the VNC server runs. This example has a simple
animation that shows coloured squares (one bounces), and a bezier curve which has a moving
control point.

    python example_animation.py

### Changing the surface

If it is necessary to change the size of the surface, the VNC server must be informed of
this fact. This is done by calling the `change_surface` method on the server, supplying
the new surface. This will issue the necessary messages to the clients which are connected
to notify them that the surface contents have changed, and send the new frame buffer at
the next request.

The following example is similar to the basic example, above, but every 2 seconds the
surface is changed in size.

    python example_new_surface.py

### Thread safety

Whilst the surface is being updated by the animation thread, the surface might need to
be supplied to a VNC client. In the prior examples, there was no consideration for this,
which might mean that partial frame content was delivered to the VNC clients. To prevent
this from happening, it is possible to use a lock around updates to the surface. This
ensures that only whole frames are delivered to the client.

The following example adds this locking to the surfaces.

    python example_locking.py
