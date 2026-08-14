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
    
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()

    def __init__(self,registry,**params):
        super().__init__(**params)
        self.registry = registry
        self._get_style()
        self._get_valid_dates()

        self._satellite = self.satellite_full.split()[0]
        self.sat_info = self.registry.sat_info[self.satellite_full]

        self._layout = pn.Card(
            self.param.year,
            self.param.satellite_full,
            **self.card,
            title="Select Satellite and Year for Analysis"
        )

    def _get_valid_dates(self):
        self.valid_min_date, self.valid_max_date = get_valid_dates(year=self.year)

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout