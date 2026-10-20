"""Shortcut to fhba/panel/app.py.

Launch the app from the project root with:
    > panel serve app.py
"""

from fhba.panel.app import build_app

if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()