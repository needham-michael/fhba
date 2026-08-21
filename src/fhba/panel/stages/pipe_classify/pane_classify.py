from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

import cartopy.crs as ccrs
import geopandas as gpd
import geoviews as gv
import holoviews as hv
import numpy as np
import pandas as pd
import panel as pn
import param
import shapely
import xarray as xr

from fhba.panel.utils import style
from fhba.viz import shp2gv

class PaneClassifyPixels(param.Parameterized):

    year = param.Integer()
    satellite_full = param.String()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()
    sat_band_subset = param.List()
    classification_methods = param.List()
    granules = param.Parameter()

    def __init__(self,granules,**params):
        super().__init__(**params)
        self.granules = granules
        self._selected_date = None
        self._get_style()
        self._build_classify_pixels_pane()

        self._layout = self._classify_pixels_pane_layout

    def _assign_date(self,date):
        self._selected_date = date
        self._classify_pixels_pane_title.object = f"## {self.satellite} | {self._selected_date} | Burnmask"

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def _build_classify_pixels_pane(self):

        self._loading_icon = pn.widgets.LoadingSpinner(value=False,size=35)

        self._load_button = pn.widgets.Button(name="Load Burnmask",**self.button_primary)
        self._classify_button = pn.widgets.Button(name="Generate Burnmask",**self.button_primary)
        self._export_button = pn.widgets.Button(name="Save Burnmask",**self.button_success)
        self._export_overwrite_checkbox = pn.widgets.Checkbox(name="Overwrite Burnmask?",value=False)


        self._classify_pixels_widgets = pn.WidgetBox(
            pn.pane.Markdown("## Burnmask Classification Controls"),
            pn.Row(
                self._load_button,
                self._classify_button,
                pn.Column(
                    self._export_button,
                    self._export_overwrite_checkbox
                ),
                self._loading_icon
            )
        )       

        self._maptiles = gv.tile_sources.CartoLight.opts(
            xlim=(self.registry.epsg_extent[0],self.registry.epsg_extent[2]),
            ylim=(self.registry.epsg_extent[1],self.registry.epsg_extent[3]),
            )

        self._county_overlay = shp2gv(self.registry.county_shp)

        self._classify_pixels_pane = pn.pane.HoloViews(self._maptiles * self._county_overlay,min_height=800,min_width=800)
        self._classify_pixels_pane_title = pn.pane.Markdown("")
        self._classify_pixels_pane_layout = pn.Column(
            self._classify_pixels_pane_title,
            self._classify_pixels_widgets,
            self._classify_pixels_pane,
        )


    # For some reason need to call self.__panel__() to display this
    # since it is a page element and not an independent pipeline stage
    def __panel__(self):
        return self.panel()

    def panel(self):
        return self._layout
