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
import rioxarray as rxr
import shapely
import xarray as xr

from fhba.classification import classify_pixels
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

        self._setup()
        self._get_style()
        self._build_classify_pixels_pane()

        self._layout = self._classify_pixels_pane_layout

    def _setup(self):
        if self._selected_date is not None:
            self._selected_granule = self.granules[self._selected_date]

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
        self._classify_button = pn.widgets.Button(name="Generate Burnmask",on_click=self._classify_pixels,**self.button_primary)
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

    def _classify_pixels(self,event):
        self._load_ds()
        self._load_userpts()
        self._get_cloudmask()

        self._burnmask = {}
        self._burnmask_conf = {}

        print("Reading lcmask")
        lcmask = rxr.open_rasterio(self.registry.path_lmask).squeeze().rename("lcmask")

        daily_mask = lcmask * self._cloudmask

        for method in self.classification_methods:
            print(f"Classification {method = }")

            self._burnmask[method], self._burnmask_conf[method] = classify_pixels(
                ds = self._classify_ds, userpts = self._selected_userpts, method = method, 
            )

        # Apply daily mask (landcover * cloudmask) to burnmask and confidence maps 
        _bm = xr.Dataset({m:self._burnmask[m] * daily_mask for m in self._burnmask})
        _bm_conf = xr.Dataset({f"{m}_conf":self._burnmask_conf[m] * daily_mask for m in self._burnmask_conf})

        daily_burnmask_file = self.registry.path_burnmask / f"{self.satellite}_{self.registry.casename}_burnmask_{self._selected_date}.nc"
        print("Saving output to {daily_burnmask_file}")

        xr.merge([_bm,_bm_conf,self._cloudmask,lcmask]).to_netcdf(daily_burnmask_file)

        print("Updating the registry")
        self.granules[self._selected_date].is_classified = True
        self.granules[self._selected_date].files.burnmask = daily_burnmask_file
        self.registry.to_json()
        print("Done.")

        pn.state.notifications.info("Classification Complete.")

        # FOR MONDAY 8/24
        # LANDCOVER MASK
        #    - Resampling (within preprocessing pipe or first-time setup?)
        #    - Combine with cloud mask for daily mask

    def _get_cloudmask(self,threshold = 0.75):
        if self.sat_info.instrument == 'viirs':
            self._cloudmask = (self._selected_ds['Clear_Sky_Confidence'] > threshold).rename("cldmsk")
            
        else:
            raise NotImplementedError(f"{self.sat_info.instrument =}")

    def _load_userpts(self):
        self._selected_userpts = gpd.read_file(
            self.granules[self._selected_date].files.user_pts
        )

    def _load_ds(self):
        self._loading_icon.value = True
        self._classify_pixels_widgets.disabled = True
        if self.sat_info.instrument == 'viirs':
            self._load_ds_viirs(date=self._selected_date)
        else:
            raise NotImplementedError(
                f"Loading instrument: {self.sat_info.instrument} not yet supported")

        self._loading_icon.value = False
        self._classify_pixels_widgets.disabled = False

    @lru_cache(maxsize=3)
    def _load_ds_viirs(self,date):
        granule_manager = self.granules[date]
        self._selected_ds = xr.open_dataset(granule_manager.files.reproj_granule)
        self._selected_ds = self._selected_ds.load()
        self._selected_ds.attrs['crs'] = ccrs.epsg(self.registry.epsg)
        self._classify_ds = self._selected_ds[[x for x in self._selected_ds.data_vars if x in self.sat_info.band_list_all]]


    # For some reason need to call self.__panel__() to display this
    # since it is a page element and not an independent pipeline stage
    def __panel__(self):
        return self.panel()

    def panel(self):
        return self._layout
