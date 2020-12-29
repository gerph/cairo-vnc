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

    python screen.py

then connect to VNC display 2 (sometimes listed as port 5902, rather than display 2) on
localhost.
