from datetime import datetime

import pandas as pd
import panel as pn
import param

from fhba.panel.utils import style, get_valid_dates

class StageSelectInstrument(param.Parameterized):

    year = param.Selector(
        default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1))
        )

    satellite_full = param.Selector(
        default='Suomi-NPP VIIRS', 
        objects=['Suomi-NPP VIIRS', 'NOAA-20 VIIRS', 'NOAA-21 VIIRS'],
        )

    sat_band_subset = param.List()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()

    def __init__(self,registry,show_band_selector=False,**params):
        super().__init__(**params)
        self.registry = registry
        self._show_band_selector = show_band_selector
        self._get_style()
        self._get_valid_dates()

        self.satellite = self.satellite_full.split()[0]
        self.sat_info = self.registry.sat_info[self.satellite_full]
        
        self._build_band_selector_pane()

        self._layout = pn.Card(pn.Row(
            pn.Column(
                pn.pane.Markdown("## Select Satellite and Year for Analysis"),
                self.param.year,
                self.param.satellite_full,
            ),
            self._band_selector_layout,
            ),**self.card,
            # title="Select Satellite and Year for Analysis"
        )

    def _get_valid_dates(self):
        self.valid_min_date, self.valid_max_date = get_valid_dates(year=self.year)

    def _build_band_selector_pane(self):
        self._band_selector = pn.widgets.MultiChoice.from_param(
            self.param.sat_band_subset,
            options=self.sat_info.band_list_all,
            value=self.sat_info.band_list_default,
            label="Select Satellite Bands"
        )

        if self.sat_info.instrument in self.registry.sat_band_defaults:
            self._band_selector.value = self.registry.sat_band_defaults[self.sat_info.instrument]

        self._band_selector_save_button = pn.widgets.Button(
            name="Save as Case Default",on_click = self._save_band_defaults,
            **self.button_primary,
        )

        self._band_selector_reset_button = pn.widgets.Button(
            name="Reset to Default",on_click = self._reset_band_defaults,
            color='default',
        )

        self._band_selector_layout = pn.Column(
            self._band_selector,
            self._band_selector_save_button,
            self._band_selector_reset_button,
            visible=self._show_band_selector
        )

    def _save_band_defaults(self,event):
        self.registry.sat_band_defaults[self.sat_info.instrument] = self._band_selector.value
        self.registry.to_json()
        pn.state.notifications.info("Satellite Bands Saved as Case Default")

    def _reset_band_defaults(self,event):
        del self.registry.sat_band_defaults[self.sat_info.instrument]
        self.registry.to_json()
        self._band_selector.value=self.sat_info.band_list_default
        pn.state.notifications.info("Satellite Bands Reset to Default List")

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        self.sat_band_subset = self._band_selector.value
        return self._layout