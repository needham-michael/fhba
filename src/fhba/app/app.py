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
from fhba.app.stage_preview_burnmaps import StagePreviewBurnmaps
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

        #  Apply stopgap measure to relax date range to entire year
        gm._relax_date_range()

        return satellite, registry, gm
        
    @param.depends('year','satellite_full')
    def view(self):

        instr = get_instructions(
            "01_instr_select_sat.md", instr_width=800, as_card=False)

        pane = pn.Column(
            instr,
            self.param.year,
            self.param.satellite_full,
            margin=(40, 40), sizing_mode='stretch_both',styles={'background': '#f0f0f0'}
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)
    
class StageSetupAgg(param.Parameterized):
    year = param.Selector(default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1)))

    @param.output(('registry',param.Parameter))
    def output(self):

        # Skip getting the satpy_area_def since no processing occurs in this portion
        # of the application. Also skip authentication with earthaccess for a similar

        registry = Registry(get_satpy_area_def=True,auth_earthaccess=False).load_json()

        if str(self.year) not in registry.granule_registry:
            registry.add_granule_registry(str(self.year))
            registry.save_json()

        return registry
    
    @param.depends('year')
    def view(self):

        instr = get_instructions(
            "07_instr_select_year.md", instr_width=800, as_card=False)

        pane = pn.Column(
            instr,
            self.param.year,
            margin=(40, 40), sizing_mode='stretch_both',styles={'background': '#f0f0f0'}
        )

        return pane
    
    def panel(self):
        return pn.Row(self.view,)
    
def build_app():

    pipeline_download = pn.pipeline.Pipeline(debug=True)
    pipeline_download.add_stage(name="Select Instrument",stage=StageSetup)
    pipeline_download.add_stage(name="Download Previews",stage=StageDownloadPreviews)
    pipeline_download.add_stage(name="Sort Images",stage=StagePreviewImages)
    pipeline_download.add_stage(name="Download Granules",stage=StageDownloadGranules)
    
    pipeline_classify = pn.pipeline.Pipeline(debug=True)
    pipeline_classify.add_stage(name="Select Instrument",stage=StageSetup)
    pipeline_classify.add_stage(name="Analyze Pixels",stage=StageAnalyze)
    pipeline_classify.add_stage(name="Categorize",stage=StageClassify)

    pipeline_aggregate = pn.pipeline.Pipeline(debug=True)
    pipeline_aggregate.add_stage(name="Select Instrument",stage=StageSetupAgg)
    pipeline_aggregate.add_stage(name="Aggregate Burn Masks",stage=StageAggregate)
    pipeline_aggregate.add_stage(name="View Burn Masks",stage=StagePreviewBurnmaps)

    # Placeholder for tab setup to separate YTD burn mask aggregation and stats
    with open("README.md") as f:
        readme_md = f.read()

    app = pn.Column(
        pn.pane.Markdown("# Flint Hills Burned Area (FHBA) Mapping Tool"),
        pn.layout.Divider(),
        pn.Tabs(
            ("README",pn.pane.Markdown(readme_md, width=800)),
            ("1. Download Granules",pipeline_download),
            ("2. Classify Pixels",pipeline_classify),
            ("3. Aggregate YTD Burn Masks",pn.Column(
                get_agg_pipeline_disclaimer(),
                pipeline_aggregate)),
            tabs_location='above',
    )
    )
        
    return app


def get_agg_pipeline_disclaimer():

    import textwrap

    disclaimer_text = textwrap.dedent(
    """
    # *DISCLAIMER*
    This section allowing the user to aggregate all YTD burnmasks is currently under active development. Bugs will exist, use with caution.
    """
    )

    return pn.pane.Alert(disclaimer_text,alert_type='warning')

if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()


