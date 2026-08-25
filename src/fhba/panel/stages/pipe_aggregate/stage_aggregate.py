import datetime
import os

import geopandas as gpd
import pandas as pd
import panel as pn
import param

from fhba.aggregate import SatelliteBurnmask, UnifiedBurnmask, get_burn_area_by_county
from fhba.viz import generate_burnmask_figure

from fhba.panel.utils import style, get_valid_dates

class StageAggregate(param.Parameterized):
    year = param.Integer()
    registry = param.Parameter()
    valid_max_date = param.String()
    valid_min_date = param.String()

    def __init__(self,**params):
        super().__init__(**params)
        self._get_style()
        self._setup()
        

        self._layout = pn.Card(pn.Column(
            pn.pane.Markdown(f"{self.year =}"),
            self._dateselector,
            self._sat_checkbox
        ))

    def _setup(self):
        
        self._get_valid_dates()

        self._loading_icon = pn.indicators.LoadingSpinner(height=35,value=False)
        self._dateselector = pn.widgets.DateRangePicker(name='Select Date Range',
            start=datetime.date(*[int(x) for x in self.valid_min_date.split("-")]),
            end=datetime.date(*[int(x) for x in self.valid_max_date.split("-")])
        )

        self._sat_checkbox = pn.widgets.CheckBoxGroup(
            options=list(self.registry.granules[str(self.year)].keys())
        )

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def _get_valid_dates(self):
        self.valid_min_date, self.valid_max_date = get_valid_dates(year=self.year)

    def panel(self):
        return self._layout