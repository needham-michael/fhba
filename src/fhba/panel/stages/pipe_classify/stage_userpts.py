
from pathlib import Path
from typing import Dict, List, Tuple

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
from fhba.viz import (nir_red_sqrt, nir_red_red, nir_red_mwir, shp2gv)
from fhba.panel.stages.pipe_classify.pane_user_annotate import PaneUserAnnotate
from fhba.panel.stages.pipe_classify.pane_classify import PaneClassifyPixels

class StageClassifyUserpts(param.Parameterized):

    year = param.Integer()
    satellite_full = param.String()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()
    sat_band_subset = param.List()
    granules = param.Parameter()

    def __init__(self,**params):
        super().__init__(**params)
        self._get_style()
        self._setup()

        self._pane_user_annotate = PaneUserAnnotate(
            granules=self.granules,rgb_composite_fn = self._rgb_composite_fn,**params)
        self._pane_classify_pixels = PaneClassifyPixels(
            granules=self.granules,**params)
        
        self._custom_tabs = {
            'SelectData':pn.Card(self._dateselect_layout,**self.card),
            'MappingPane': pn.Column(
                self._back_button,
                pn.Tabs(
                    ("Instructions",pn.pane.Markdown("Instructions")),
                    ("Map",pn.Row(self._pane_user_annotate,self._pane_classify_pixels,)),
                    active=1,dynamic=False
                )
        )}

        self._layout = pn.Card(objects=self._custom_tabs['SelectData'],**self.card)
        # self._layout = self._custom_tabs['SelectData']

    def _setup(self):

        self._back_button = pn.widgets.Button(name="Back to Selection",on_click=self._click_back,**self.button_warning)
                
        self.granules = self.registry.granules[str(self.year)][self.satellite_full]

        self._rgb_composite_fn =  {
            "NIR-RED-SQRT":nir_red_sqrt,
            "NIR-RED-RED":nir_red_red,
            "NIR-RED-MWIR":nir_red_mwir
        }

        self._rgb_composite_options_all = list(self._rgb_composite_fn.keys())

        self._build_selectdata_pane()

    def _build_selectdata_pane(self):
        self._build_table_df()

        self._table = pn.widgets.Tabulator(self._classifying_df, show_index=False)
        
        self._load_img_button = pn.widgets.Button(name="Load Image",**self.button_primary,on_click=self._load_img)
        self._loading_icon = pn.widgets.LoadingSpinner(value=False,size=35)
        self._date_selector = pn.widgets.Select(
            label="Select Date",options = list(self._classifying_df['date']),value=self._classifying_df['date'].iloc[0])

        self._composite_selector = pn.widgets.Select(
            label="Select RGB Composite",
            options=self._rgb_composite_options_all,
            value=self._rgb_composite_options_all[0]
        )

        self._dateselect_layout = pn.Column(
            self._composite_selector,
            self._date_selector,
            pn.Row(self._load_img_button,self._loading_icon),
            pn.pane.Markdown("### Granule Classification Status"),
            self._table
        )

    def _build_table_df(self):
        date_col = [d for d in self.granules if self.granules[d].is_processed]
        band_col = [self.granules[d].processed_bands for d in date_col]
        usr_col = [self.granules[d].is_user_categorized for d in date_col]
        alg_col = [self.granules[d].is_classified for d in date_col]

        self._classifying_df = pd.DataFrame(data={
            'date':date_col,
            'Processed Bands':band_col,
            'User Classification Status':usr_col,
            'Classification Algorithm Applied':alg_col,
        })

    def _load_img(self,event):
        self._loading_icon.value = True
        self._selected_date = self._date_selector.value
        self._selected_composite = self._composite_selector.value
        pn.state.notifications.info(f"Loading {self._selected_composite} Composite for date: {self._selected_date}")     
        self._pane_user_annotate._load_img(event,date=self._selected_date,composite=self._selected_composite)
        self._pane_classify_pixels._assign_date(date=self._date_selector.value)
        self._layout.objects = [self._custom_tabs['MappingPane']]
        self._loading_icon.value = False

    def _click_back(self,event):
        self._build_table_df() # Reload to ensure updates to table
        self._table.value = self._classifying_df
        self._layout.objects = [self._custom_tabs['SelectData']]

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout