# CairoVNC - VNC server for Cairo surfaces

## Introduction

This repository holds an implementation of a VNC server that can supply the contents of a
PyCairo surface to multiple clients.

Currently a work in progress, it is intended to incorporate most of the simple features of
VNC screen, together with the keyboard and mouse input.

The code is intentionally compatible between Python 2 and Python 3.

## Usage

The code for the VNC server is all in the `cairovnc` directory. It'll be at varying degrees
of completeness as time goes on.

### Structure

The CairoVNC package is structured such that users of the system (the 'animator', within this
documentation) should usually only access the CairoVNCServer and CairoVNCOptions objects.
The 'animator' controls the Cairo surface, and the CairoVNCServer object manages the server
and its connections.

The CairoVNCServer provides a VNC server which will spawn a new thread for each connection.
These connections may come and go, and their updates will be managed by the CairoVNCServer
system. The connections may supply events (such as mouse operations and key presses) to
the animator through a thread safe queue whcih it may consume.

The animator may prevent access to the surface it is updating through a thread lock to prevent
unsafe operations and incomplete frames.

### Creating a server

The CairoVNC server can be created with the `cairovnc.CairoVNCServer` object creation. This
object takes the surface which should be used as the only required parameter. It also
takes a number of optional named parameters which are commonly used:

* `host` and `port`: Supplies the address and port that the server should listen on.
* `surface_lock`: Supplies a `threading.Lock` object which will be used around all access to the surface.
* `options`: Supplies a `cairovnc.CairoVNCOptions` object which provides all the other exposed configurables.

Creating the `CairoVNCServer` object does not begin listening immediately. The server can be
started and stopped under the control of the animator system. The server can run in one of three
models:

* Daemonised: This model is activated by using the `daemonise` method. This will start a separate thread for the server. It will run until the process exits, or the `stop` method is called (from another thread).
* Blocking: This model is activated by using the `serve_forever` method. This will start the server on the current thread. It will run until the `stop` method is called (from another thread).
* Polled: This model is activated by using the `start` method to start the server listening, and must then be polled for connections with the `poll` method. The `poll` method can block for a period or return immediately. When the server is no longer needed the `stop` method should be called.

It is not expected that the Polled model be used often; the Daemonised and Blocking models are more likely to be useful to users.

The basic creation of a server might be thus:

```
server = cairovnc.CairoVNCServer(port=5900, surface=surface)
server.serve_forever()
```

The more advanced cases may create an options object, and place the server into a separate thread
to run.

```
options = cairovnc.CairoVNCOptions(port=5900, password='secret')
options.max_clients = 20

server = cairovnc.CairoVNCServer(surface=surface, surface_lock=surface_lock,
                                 options=options)
server.daemonise()

while still_animating:
    with surface_lock:
        render_frame()
    time.sleep(frame_period)

server.stop()
```

### Events

Events from the remote connection are delivered to a queue and can be retrieved by calling the
`get_event` method. This method takes a timeout to allow it to poll for a period or to return
immediately. The events that are delivered will depend on the client's capabilities. Not all events will be of interest to the animator. The event type can be recognised either by checking
the type of `VNCEvent` instance that has been supplied, or examining the `name` property of the event.

There are currently 3 events which can be delivered:

* `VNCEventKey`: (name `key`) \
This event delivers a key press or release event.
    * Property `key`: Contains the VNC key symbol codes for the event. These are largely the ASCII codes with some special values for control keys. Consult the `events.py` source for these constants.
    * Property `down`: `True` if the event is for a key press, `False` if the event is for a key release. The event may be retriggered if auto-repeat is enabled on the client system, resulting in multiple `True` events being delivered before a `False` indicates the release.
* `VNCEventMove`: (name `move`) \
This event delivers a pointer movement.
    * Property `x`, `y`: Contains the coordinates of the pointer within the frame buffer, as positive pixel offsets from the top left corner.
    * Property `buttons`: Contains a bitmask of the mouse buttons which are currently active. Bit 0 corresponds to the first mouse button. Protocol limitations mean that only the first 8 buttons will be delivered reliably by this event.
* `VNCEventClick`: (name `click`) \
This event delivers a pointer click or release; it is the pointer analogue of the key event.
    * Property `x`, `y`: Contains the coordinates of the pointer within the frame buffer, as positive pixel offsets from the top left corner.
    * Property `button`: Contains the mouse button number, starting from 0 for the first button.
    * Property `down`: `True` if the event is for a click, `False` if the event is for a release.

## Examples

In all the examples they are listening on VNC display 2 (port 5902).

Note: For reasons that are unclear, it is impossible to connect to a VNC without a password
using the macOS Screen Sharing tool. For these examples without a password, you are
recommended to use a different client.

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

### Passwords

Passwords can be supplied for connection to the server. It is required to have the `des`
module installed for passwords to be supported. It is possible to specify two passwords
for the server - the standard password which uses the options as supplied, and a
'read only' password, which will enable the 'read_only' option if it is used.

The following example adds passwords to the server. The passwords are `password` and
`readonly`.

    python example_password.py

### Input Events (keys and pointer)

The server can accept key and pointer events (when not in 'read only' mode). These
events are placed on a queue and can be read out by the animator. Events will only
be delivered for clients which are not 'read only', so by setting the `read_only`
option, the events will never be delivered.

Note: If the events are not read by the animator, the VNC clients will block
      waiting for the queues to drain.

The following example adds input events, allowing one of the control points to
be moved with the pointer, and changing the colour of the squares when the mouse
buttons are clicked.

    python example_input.py

### Desktop name

The desktop name is used to identify what the server calls the desktop that is being
served. It can be supplied on creation of the server, in the options object or as
a parameter. The name can be changed whilst the server is running by calling the
`change_name` method on the server object, with the new name.

Support for changing the deslktop name is not universal, and some clients will
remain with the name which was initially delivered.

    python example_name.py

## Testing

There are no test suites as yet. It's all manually tested, I'm afraid.
