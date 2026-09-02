import importlib
import json
import os
import shutil

from pathlib import Path

import cartopy.crs as ccrs
import geoviews as gv
import numpy as np
import pandas as pd
import panel as pn
import param

from fhba.panel.utils import style, bbox_is_valid, validate_directory
from fhba.schemas import Registry
from fhba.schemas.sync import json2cases, json2reg, cases2json

from fhba.panel.instructions import Instructions

from fhba.panel.stages import (
    StageSelectInstrument, StageDownloadWorldview, StageSortTruecolor, StageDownloadGranules,
    StageSelectBlendMethod, StageProcessGranules, StageClassifyUserpts, StageSelectYear, 
    StageAggregate, StageViewBurnmasks
    )

class PageAnalysisPipeline(param.Parameterized):

    selected_casename = param.String()
    ready = param.Boolean(default=False)
    advance_to = param.String(default=None)

    def __init__(self, **params):

        super().__init__(**params)
        self._get_style()
        self._setup()

        self._layout_header = pn.Row(
            self.back,
            # pn.pane.Markdown(f"# Case: {self.selected_casename}"),
        )

        self._layout_tabs = pn.Tabs(
            ("Case Info",pn.Column(
                self._refresh_json,self._json_viewer,)),
            ("Missing Files",pn.Card(
                self._reset_missing_files_btn,self._missing_files_json,**self.card)),
            ("Instructions",Instructions),
            ("1. Download Granules", self._pipeline_download_layout),
            ("2. Process Granules", self._pipeline_process_layout),
            ("3. Classify Granules", self._pipeline_classify_layout),
            ("4. Aggregate Burnmasks", self._pipeline_aggregate_layout),
            dynamic=True,
            active=2,
        )

        self._layout = pn.Card(pn.Column(
            self._layout_header,
            pn.layout.Divider(),
            self._layout_tabs
            ),header=pn.pane.Markdown(f"# Case: {self.selected_casename}"),**self.card)

    def _setup(self):
        # Navigation Buttons
        self.back    = pn.widgets.Button(name="Back to Case Selection",**self.button_warning)
        self.back.on_click(lambda e: self._advance("Start"))

        
        self._refresh_json = pn.widgets.Button(name="Refresh Case Info",on_click=self._refresh_case_info,**self.button_success)
        self._reset_missing_files_btn = pn.widgets.Button(name="Reset Missing Files",on_click=self._reset_missing_files,**self.button_warning)

        self._json2cases()
        self._json_registry_filename = self._fhba_cases.cases[self.selected_casename] / f"fhba_{self.selected_casename}.json"
        self._json2reg()
        self._reg.audit_granules()
        self._missing_files_json = pn.pane.JSON(self._reg._audited_files,depth=-1)

        self._json_viewer = pn.widgets.JSONEditor(
            value=self._reg.model_dump(mode='json'),selection=[],mode='view',
        sizing_mode='stretch_width')

        # Analysis Pipelines
        self._build_pipeline_download()
        self._build_pipeline_process()
        self._build_pipeline_classify()
        self._build_pipeline_aggregate()

    def _reset_missing_files(self,event):
        self._json2reg()
        self._reg.audit_granules()
        self._reg.reset_missing()
        self._reg.audit_granules()
        self._missing_files_json.object = self._reg._audited_files
        self._reg.to_json()
        pn.state.notifications.success("Missing Files Removed from Registry.")

    def _refresh_case_info(self,event):
        self._json2reg()
        self._json_viewer.value = self._reg.model_dump(mode='json')
        pn.state.notifications.success("Case Info Updated.")
        

    def _build_pipeline_download(self):
        _pipe = pn.pipeline.Pipeline(
            stages=[
                ('Select',StageSelectInstrument(registry=self._json2reg(return_obj=True))),
                ('DownloadWorldview',StageDownloadWorldview),
                ('SortTrueColor',StageSortTruecolor),
                ('DownloadGranules',StageDownloadGranules)
            ],
            debug=True,ready_parameter='ready'
        )  

        self._pipeline_download_layout = _pipe
        self._pipeline_download = _pipe

    def _build_pipeline_process(self):
        _pipe = pn.pipeline.Pipeline(
            stages=[
                ('Select',StageSelectInstrument(registry=self._json2reg(return_obj=True),show_band_selector=True)),
                ('Mosaic',StageSelectBlendMethod),
                ('Process',StageProcessGranules),
            ],
            debug=True,ready_parameter='ready'
        )     

        self._pipeline_process_layout = _pipe
        self._pipeline_process = _pipe

    def _build_pipeline_classify(self):
        _pipe = pn.pipeline.Pipeline(
            stages=[
                ('Select',StageSelectInstrument(registry=self._json2reg(return_obj=True),show_classification_selector=True)),
                ('Classify Points',StageClassifyUserpts)
            ],
            debug=True,ready_parameter='ready'
        )     

        self._pipeline_classify_layout = _pipe
        self._pipeline_classify = _pipe

    def _build_pipeline_aggregate(self):
        _pipe = pn.pipeline.Pipeline(
            stages=[
                ('Select',StageSelectYear(registry=self._json2reg(return_obj=True))),
                ('Aggregate Burnmasks',StageAggregate),
                ('View Burnmasks',StageViewBurnmasks),
            ],
            debug=True,ready_parameter='ready'
        )

        self._pipeline_aggregate_layout = _pipe
        self._pipeline_aggregate = _pipe

    def _advance(self,dest):
        self.advance_to = dest
        self.ready = True

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def _json2cases(self):
        self._fhba_cases_json, self._fhba_cases = json2cases()

    def _json2reg(self,return_obj=True):
        self._reg = json2reg(self._json_registry_filename)
        if return_obj:
            return self._reg

    def panel(self):
        self.ready = False
        self.advance_to = None
        return self._layout