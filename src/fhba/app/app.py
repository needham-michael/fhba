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

pn.extension('tabulator','terminal')
pn.extension(notifications=True)

class StageSetup(param.Parameterized):

    year = param.Selector(default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1)))
    satellite_full = param.Selector(
        default='Suomi-NPP VIIRS', 
        objects=['Suomi-NPP VIIRS', 'NOAA-20 VIIRS', 'NOAA-21 VIIRS'],
        )

    @param.output(('satellite',param.String),('registry',param.Parameter),('gm',param.Parameter))
    def output(self):

        # Skip getting the satpy_area_def since no processing occurs in this portion
        # of the application. Also skip authentication with earthaccess for a similar

        registry = Registry(get_satpy_area_def=True,auth_earthaccess=False).load_json()

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

        instr = get_instructions("01_instr_select_sat.md", instr_width=800)

        pane = pn.Column(
            instr,
            self.param.year,
            self.param.satellite_full,
            margin=(40, 40), sizing_mode='stretch_both',styles={'background': '#f0f0f0'}
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)

def build_app():
    
    pipeline_daily = pn.pipeline.Pipeline(debug=True)
    pipeline_daily.add_stage(name="Select Instrument",stage=StageSetup)
    pipeline_daily.add_stage(name="Download Previews",stage=StageDownloadPreviews)
    pipeline_daily.add_stage(name="Sort Images",stage=StagePreviewImages)
    pipeline_daily.add_stage(name="Download Granules",stage=StageDownloadGranules)
    pipeline_daily.add_stage(name="Analyze Pixels",stage=StageAnalyze)
    pipeline_daily.add_stage(name="Categorize",stage=StageClassify)

    pipeline_ytd = pn.pipeline.Pipeline(debug=True)
    pipeline_ytd.add_stage(name="Select Instrument",stage=StageSetup)
    pipeline_ytd.add_stage(name="Aggregate Burn Masks",stage=StageAggregate)

    # Placeholder for tab setup to separate YTD burn mask aggregation and stats
    with open("README.md") as f:
        readme_md = f.read()

    app = pn.Column(
        pn.pane.Markdown("# Flint Hills Burned Area (FHBA) Mapping Tool"),
        pn.layout.Divider(),
        pn.Tabs(
            ("Daily Images",pipeline_daily),
            ("YTD Burn Mask",pipeline_ytd),
            ("README",pn.pane.Markdown(readme_md, width=800)),
    )
    )
        
    return app

if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()