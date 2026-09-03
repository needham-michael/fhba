import geoviews as gv
import numpy as np
import panel as pn
import param

gv.extension("bokeh")

from fhba.panel.setup import VersionInfo
from fhba.panel.pages import PageSelectCase, PageAnalysisPipeline
from fhba.panel.docs import DocsPane

pn.extension("ace",'tabulator','terminal','modal',"jsoneditor",'floatpanel')
pn.extension(notifications=True)

class AppPages(param.Parameterized):

    case_name = param.String(default="")
    ready = param.Boolean(default=False)
    advance_to = param.String(default=None)

    def __init__(self,**params):
        super().__init__(**params)
        self._json2cases()

        if self._fhba_cases.cases == {}:
            existing_cases = [None]
        else:
            existing_cases = list(self._fhba_cases.cases.keys())

        bbox_default = np.array([-97.75, 35.75, -95.25, 39.75])

        page_select_case = PageSelectCase(
            existing_cases=existing_cases,bbox_default=bbox_default,)

        # page_analysis_pipeline = PageAnalysisPipeline()

        self._pipeline = pn.pipeline.Pipeline(
            stages=[
                    ('Start', PageSelectCase(
                        existing_cases=existing_cases,
                        bbox_default=bbox_default,)),
                    ('AnalysisPipeline', PageAnalysisPipeline),
                ],
                next_parameter="advance_to",
                ready_parameter="ready",
                auto_advance=True,
                debug=True,
            )

        self._layout = pn.Column(
            self._pipeline.stage
        )

    def _json2cases(self):
        from fhba.schemas.sync import json2cases
        self._json_cases_filename, self._fhba_cases = json2cases()

    def panel(self):
        return self._layout
    
def build_app():

    is_dev_mode = pn.config.autoreload
    header_background = "#0072B2"
    app_title = "Flint Hills Burned Area"
    if is_dev_mode:
        header_background = "#E69F00"
        app_title = "Flint Hills Burned Area (DEV MODE)"


    app = pn.template.MaterialTemplate(
        sidebar=DocsPane()._layout,
        main=[
            VersionInfo()._layout,
            AppPages()._layout,
        ],
        header_background=header_background,
        title = app_title,
        collapsed_sidebar=True,
        sidebar_width=275
    )
        
    return app

if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()