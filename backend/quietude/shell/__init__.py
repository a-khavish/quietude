# Quietude - a personal assistant that runs on your own machine.
# Copyright (C) 2026 Khavish Auckaloo
# SPDX-License-Identifier: GPL-3.0-or-later
"""The native window shell.

Deliberately importable only by the interpreter that runs it. Nothing in
the rest of the app imports from here - the window is spawned as a
process, by a Python chosen at runtime, because PyGObject belongs to the
distribution's interpreter and the virtualenv's job is to not care about
that.
"""
