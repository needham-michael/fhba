import time

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
from fhba.viz import shp2gv, bm_rgb
from fhba.panel.stages.pipe_classify.pane_user_annotate import (
    initialize_userpoly, initialize_userpoints, pts2gdf, poly2gdf, poly2pts
)

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
        self._qa_mask = None
        self._lcmask = None

        self._setup()
        self._get_style()
        self._build_classify_pixels_pane()

        self._layout = self._classify_pixels_pane_layout

    def _setup(self):
        if self._selected_date is not None:
            self._selected_granule = self.granules[self._selected_date]

    def _assign_date(self,date):
        self._selected_date = date
        self._selected_granule = self.granules[self._selected_date]
        self._classify_pixels_pane_title.object = f"## {self.satellite} | {self._selected_date} | Burnmask"
        self._load_button.disabled = not self._selected_granule.is_classified
        self._qa_button.disabled = not self._selected_granule.is_classified

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def _build_classify_pixels_pane(self):

        self._loading_icon = pn.widgets.LoadingSpinner(value=False,size=35)

        self._load_button = pn.widgets.Button(name="Load Burnmask",on_click=self._load_burnmask,**self.button_primary,disabled=True,)
        self._classify_button = pn.widgets.Button(name="Generate Burnmask",on_click=self._classify_pixels,**self.button_primary)
        self._export_button = pn.widgets.Button(name="Save Burnmask",**self.button_success)
        self._qa_button = pn.widgets.Button(name="Apply QA Masking",on_click=self._apply_qa,**self.button_warning,)
        self._export_overwrite_checkbox = pn.widgets.Checkbox(name="Overwrite Burnmask?",value=False)

        self._qa_points, self._qa_point_stream = initialize_userpoints(
            crs=ccrs.epsg(self.registry.epsg),tooltip='QA Points',color='#38aed9'
        )
        self._qa_polys, self._qa_poly_stream = initialize_userpoly(
            crs=ccrs.epsg(self.registry.epsg),tooltip='QA Polygon',color='#38aed9'
        )

        self._classify_pixels_widgets = pn.WidgetBox(
            pn.pane.Markdown("## Burnmask Classification Controls"),
            pn.Row(
                self._load_button,
                self._classify_button,
                self._qa_button,
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

        self._classify_pixels_pane = pn.pane.HoloViews(
            self._maptiles * self._county_overlay*self._qa_points*self._qa_polys,
            min_height=800,min_width=800)
        
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

        daily_mask = self._lcmask * self._cloudmask

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

        xr.merge([_bm,_bm_conf,self._cloudmask,self._lcmask]).to_netcdf(daily_burnmask_file)

        print("Updating the registry")
        self.granules[self._selected_date].is_classified = True
        self.granules[self._selected_date].files.burnmask = daily_burnmask_file
        self.registry.to_json()
        print("Done.")

        pn.state.notifications.info("Classification Complete.")
        self._load_button.disabled = False

    def _get_cloudmask(self,threshold = 0.75):
        if self.sat_info.instrument == 'viirs':
            self._cloudmask = (self._selected_ds['Clear_Sky_Confidence'] > threshold).rename("cldmsk")
            
        else:
            raise NotImplementedError(f"{self.sat_info.instrument =}")

    def _load_burnmask(self,event):
        self._loading_icon.value = True
        self._loading_icon.name = "Loading Preliminary Burnmask..."
        self._load_ds()
        self._merge_burnmasks()
        self._apply_majority_voting()

        self._merged_burnmask.attrs['crs'] = ccrs.epsg(self.registry.epsg)

        if self._qa_mask is not None:
            pn.state.notifications.info("Applying QA Mask to Burnmask")
            print(f"{self._qa_mask = }")
            self._merged_burnmask_qa = self._merged_burnmask * self._qa_mask

            self._burnmask_qa_diff = self._merged_burnmask - self._merged_burnmask_qa

            self._gv_rgv = bm_rgb(bm=self._merged_burnmask_qa,rgb_color=(168,84,50)).opts(alpha=0.50) *\
                bm_rgb(bm=self._burnmask_qa_diff,rgb_color=(56,174,217)).opts(alpha=0.50)

        else:
            
            self._gv_rgv = bm_rgb(bm=self._merged_burnmask,rgb_color=(168,84,50)).opts(alpha=0.50)

        self._classify_pixels_pane.object = \
            self._maptiles * self._county_overlay * self._gv_rgv * self._qa_points * self._qa_polys

        time.sleep(0.75) # Ensure the loading icon keeps spinning while the image renders
        self._loading_icon.name = ""
        self._loading_icon.value = False

    def _apply_qa(self,event,xname='Longitude',yname='Latitude'):

        pn.state.notifications.info("Apply QA")

        self._qa_points_gdf = pts2gdf(self._qa_point_stream,xname,yname,isBurned=0)
        self._qa_poly_gdf = poly2gdf(self._qa_poly_stream,isBurned=0,ds=self._classify_ds)

        self._qa_gdf = pd.concat(
            [self._qa_points_gdf,self._qa_poly_gdf]
        ).reset_index()

        poly_data = {'xs':[],'ys':[]}

        for p in self._qa_gdf.buffer(
            np.sqrt(self.registry.resolution[0]*self.registry.resolution[1])
            ).geometry:

            poly_data['xs'].append(list(p.boundary.xy[0]))
            poly_data['ys'].append(list(p.boundary.xy[1]))

        if self._qa_mask is None:
            self._qa_mask = xr.ones_like(self._lcmask)

        self._qa_mask = self._qa_mask * xr.DataArray(
            1-poly2pts(self._classify_ds,poly_data,return_mask=True).T,
            coords=self._classify_ds.coords,dims=self._classify_ds.dims
        )

        self._qa_mask.attrs['crs'] = ccrs.epsg(self.registry.epsg)

        self._load_burnmask(event)    



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
        self._selected_ds = xr.open_dataset(granule_manager.files.burnmask)
        self._selected_ds = self._selected_ds.load()
        self._selected_ds.attrs['crs'] = ccrs.epsg(self.registry.epsg)
        self._classify_ds = self._selected_ds[[x for x in self._selected_ds.data_vars if x in self.classification_methods]]

        if self._lcmask is None:
            print("Reading lcmask")
            self._lcmask = rxr.open_rasterio(self.registry.path_lmask).squeeze().rename("lcmask")

    def _merge_burnmasks(self):
        self._merged_burnmask = xr.zeros_like(self._classify_ds[self.classification_methods[0]])

        for m in self.classification_methods:
            self._merged_burnmask += self._classify_ds[m]

    def _apply_majority_voting(self):
        n_methods = len(self.classification_methods)
        thr = (n_methods // 2) + 1

        self._merged_burnmask = (self._merged_burnmask >= thr).astype(int)

    # For some reason need to call self.__panel__() to display this
    # since it is a page element and not an independent pipeline stage
    def __panel__(self):
        return self.panel()

    def panel(self):
        return self._layout
