import importlib
import os
from datetime import datetime

import holoviews as hv
import geopandas as gpd
import numpy as np
import pandas as pd
import panel as pn
import param

from fhba.registry import Registry
from fhba.app.stage_download_previews import StageDownloadPreviews
from fhba.app.stage_preview_images import StagePreviewImages
from fhba.app.stage_download_granules import StageDownloadGranules
from fhba.app.stage_analyze import StageAnalyze
from fhba.app.stage_classify import StageClassify
from fhba.app.stage_aggregate import StageAggregate
from fhba.app.utils import get_instructions

pn.extension('tabulator', 'terminal')
pn.extension(notifications=True)

# Minimum data year for each satellite (used for validation)
_SATELLITE_MIN_YEAR = {
    'Suomi-NPP':   2012,
    'NOAA-20':     2018,
    'NOAA-21':     2023,
    'AQUA':        2002,
    'TERRA':       2000,
    'Landsat-8':   2013,
    'Landsat-9':   2021,
    'Sentinel-2': 2015,
}

_SATELLITE_DISPLAY_NAMES = [
    'Suomi-NPP VIIRS',
    'NOAA-20 VIIRS',
    'NOAA-21 VIIRS',
    'AQUA MODIS',
    'TERRA MODIS',
    'Landsat-8 OLI',
    'Landsat-9 OLI',
    'Sentinel-2 MSI',
]


class StageSetup(param.Parameterized):

    year = param.Selector(
        default=datetime.now().year,
        objects=list(range(2012, datetime.now().year + 1))
    )
    satellite_full = param.Selector(
        default='Suomi-NPP VIIRS',
        objects=_SATELLITE_DISPLAY_NAMES,
    )

    def __init__(self, **params):
        super().__init__(**params)
        # Initialise the year list for the default satellite
        self._update_year_objects()

    @param.depends('satellite_full', watch=True)
    def _update_year_objects(self):
        """Restrict the year selector to valid years for the chosen satellite."""
        sat_key = self.satellite_full.split()[0]  # e.g. 'Suomi-NPP', 'NOAA-20', 'Landsat-8'
        min_year = _SATELLITE_MIN_YEAR.get(sat_key, 2012)
        max_year = datetime.now().year
        valid_years = list(range(min_year, max_year + 1))
        self.param.year.objects = valid_years
        # Clamp current value if it's below the new minimum
        if self.year < min_year:
            self.year = min_year

    @param.output(('satellite', param.String), ('registry', param.Parameter), ('gm', param.Parameter))
    def output(self):

        sat_key = self.satellite_full.split()[0]   # 'Suomi-NPP', 'NOAA-20', 'Landsat-8', etc.

        min_year = _SATELLITE_MIN_YEAR.get(sat_key, 2012)

        if self.year < min_year:
            pn.state.notifications.error(
                f"{self.satellite_full} data is only available from {min_year}. "
                f"Please select a valid year.",
                duration=6000)
            raise ValueError(f"Invalid year {self.year} for {self.satellite_full}.")

        # Don't load the full registry here to improve startup speed, just ensure the 
        # application state is loaded.
        registry = Registry(get_satpy_area_def=False, auth_earthaccess=False).load_json()

        satellite = sat_key

        if str(self.year) not in registry.granule_registry:
            registry.add_granule_registry(str(self.year))
            registry.save_json()

        if satellite not in registry.granule_registry[str(self.year)].satellites:
            registry[str(self.year)].add_satellite(satellite)
            registry.save_json()

        gm = registry[str(self.year)][satellite]

        return satellite, registry, gm

    @param.depends('year', 'satellite_full')
    def view(self):

        instr = get_instructions("01_instr_select_sat.md", instr_width=800)

        pane = pn.Column(
            instr,
            self.param.year,
            self.param.satellite_full,
            margin=(40, 40), sizing_mode='stretch_both', styles={'background': '#f0f0f0'}
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)


def build_app():

    pipeline = pn.pipeline.Pipeline(debug=True)
    pipeline.add_stage(name="Select Year and Instrument",  stage=StageSetup)
    pipeline.add_stage(name="Download Preview Images",     stage=StageDownloadPreviews)
    pipeline.add_stage(name="Preview and Categorize Images", stage=StagePreviewImages)
    pipeline.add_stage(name="Download Granules",           stage=StageDownloadGranules)
    pipeline.add_stage(name="Analyze Pixels",              stage=StageAnalyze)
    pipeline.add_stage(name="Classify Burn Scars",         stage=StageClassify)
    pipeline.add_stage(name="Aggregate Burn Scars",        stage=StageAggregate)

    return pipeline


if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()
