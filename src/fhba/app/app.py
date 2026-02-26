import importlib
import os
from datetime import datetime

import holoviews as hv
import geoviews as gv
import geopandas as gpd
import numpy as np
import pandas as pd
import panel as pn
import param
import rasterio
import rioxarray as rxr

from fhba.registry import Registry
from fhba.app.stage_download_previews import StageDownloadPreviews
from fhba.app.stage_preview_images import StagePreviewImages
from fhba.app.stage_download_granules import StageDownloadGranules
from fhba.app.stage_analyze import StageAnalyze
from fhba.app.stage_classify import StageClassify
from fhba.app.utils import get_instructions

pn.extension('tabulator','terminal')
pn.extension(notifications=True)

class StageSetup(param.Parameterized):

    year = param.Selector(default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1)))
    satellite_full = param.Selector(default='Suomi-NPP VIIRS', objects=['Suomi-NPP VIIRS', 'NOAA-20 VIIRS', 'NOAA-21 VIIRS', 'AQUA MODIS', 'TERRA MODIS'])

    @param.output(('satellite',param.String),('registry',param.Parameter),('gm',param.Parameter))
    def output(self):

        # Skip getting the satpy_area_def since no processing occurs in this portion
        # of the application. Also skip authentication with earthaccess for a similar

        registry = Registry(get_satpy_area_def=False,auth_earthaccess=False).load_json()

        satellite = self.satellite_full.split()[0]

        if str(self.year) not in registry.granule_registry:
            registry.add_granule_registry(str(self.year))
            registry.save_json()

        if satellite not in registry.granule_registry[str(self.year)].satellites:
            registry[str(self.year)].add_satellite(satellite)
            registry.save_json()

        gm = registry[str(self.year)][satellite]

        return satellite, registry, gm
        
    @param.depends('year','satellite_full')
    def view(self):

        instr = get_instructions("stage1.md", instr_width=250)

        pane = pn.Row(
            instr,
            pn.Column(
            pn.pane.Markdown("## Select Analysis Year and Satellite Instrument"),
            self.param.year,
            self.param.satellite_full,
            margin=(40, 10), width=800,styles={'background': '#f0f0f0'}
        ))
        
        return pane

    def panel(self):
        return pn.Row(self.view,)

def build_app():
    
    pipeline = pn.pipeline.Pipeline(debug=True)
    pipeline.add_stage(name="Select Year and Instrument",stage=StageSetup)
    pipeline.add_stage(name="Download Preview Images",stage=StageDownloadPreviews)
    pipeline.add_stage(name="Preview and Categorize Images",stage=StagePreviewImages)
    pipeline.add_stage(name="Download Granules",stage=StageDownloadGranules)
    pipeline.add_stage(name="Analyze Pixels",stage=StageAnalyze)
    pipeline.add_stage(name="Euclidean Categorization",stage=StageClassify)
        
    return pipeline

if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()