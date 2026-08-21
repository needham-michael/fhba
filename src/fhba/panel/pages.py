import json
import os
import shutil

from importlib import resources
from pathlib import Path

import cartopy.crs as ccrs
import geoviews as gv
import numpy as np
import panel as pn
import param

from fhba.panel.utils import style, bbox_is_valid, validate_directory
from fhba.schemas import Registry
from fhba.schemas.sync import json2cases, json2reg, cases2json

from fhba.panel.instructions import Instructions

from fhba.panel.stages import (
    StageSelectInstrument, StageDownloadWorldview, StageSortTruecolor, StageDownloadGranules,
    StageSelectBlendMethod, StageProcessGranules, StageClassifyUserpts
    )


class PageSelectCase(param.Parameterized):

    selected_casename = param.String()
    ready = param.Boolean(default=False)
    advance_to = param.String(default=None)

    def __init__(self, bbox_default, existing_cases = None, **params):
        super().__init__(**params)
        self._json2cases()

        if existing_cases is None:
            existing_cases = [None]

        # Set Default Attributes
        self._existing_cases = existing_cases
        self._bbox_default = bbox_default

        self._get_style()
        self._setup()

        layout_existing_case = pn.Card(pn.Column(
            pn.Row(self._existing_case_selector,self._existing_case_load_button)
            ),**self.card,title="Load Existing Case")

        layout_newcase = pn.Card(pn.Row(
            self.gv_map,
            pn.Column(
                pn.Row(self._newcase_casename,self._newcase_resolution),
                pn.layout.Divider(),
                pn.Row(
                    self._newcase_bbox_input,
                    self._newcase_button_show_bbox,
                ),
                self._newcase_button_reset_bbox,
                pn.layout.Divider(),
                pn.Row(
                    self._newcase_data_dir_input,
                    self._newcase_button_data_dir_valid,
                ),
                self._newcase_data_dir_printout,
                pn.layout.Divider(),
                pn.Row(
                    self._newcase_output_dir_input,
                    self._newcase_button_output_dir_valid,
                ),
                self._newcase_output_dir_printout,
                pn.layout.Divider(),
                self._newcase_button_create
            ),
        ),**self.card,title="Create New Case")

        layout_delcase = pn.Card(pn.Column(
            pn.Row(self._delcase_selector,self._delcase_modal_open),
            self._delcase_checkbox,
            self._delcase_modal,
        ),**self.card,title="Delete Case")

        self._layout = pn.Tabs(
            ("Select Case",pn.Column(layout_existing_case,layout_newcase,layout_delcase)),
            ("About",pn.pane.Markdown("# About"))
        )

    def _setup(self):
        # EXISTING CASE ATTRS
        # - NONE

        # EXISTING CASE WIDGETS
        self._existing_case_selector = pn.widgets.Select(options=self._existing_cases)
        self._existing_case_load_button_disabled = (self._existing_cases == [None])
        self._existing_case_load_button = pn.widgets.Button(
            label="Load Case",on_click=self._click_load_case,**self.button_success,disabled=self._existing_case_load_button_disabled) 

        self._existing_case_selector.value = self._existing_cases[0]
        # self._existing_case_selector.param.watch(self._update_casename,"value")

        # NEW CASE ATTRS
        self._newcase_data_dir = None
        self._newcase_data_dir_valid = False
        self._newcase_output_dir = None
        self._newcase_output_dir_valid = False
        self._newcase_bbox = None
        self._newcase_bbox_valid = False

        # NEW CASE WIDGETS
        self._newcase_casename = pn.widgets.TextInput(
            label="New Casename",placeholder="new_casename")
        self._newcase_resolution = pn.widgets.ArrayInput(
            label="New Case Spatial Resolution Meters [dx, dy]",value=np.array([500,500]))
        self._newcase_bbox_input = pn.widgets.ArrayInput(
            label="Bounding Box [minlon, minlat, maxlon, maxlat]",value=self._bbox_default)
        self._newcase_button_show_bbox = pn.widgets.Button(
            label="Validate Bounding Box",on_click=self._show_bbox,**self.button_primary) 
        self._newcase_button_reset_bbox = pn.widgets.Button(
            label="Reset Bounding Box",on_click=self._reset_bbox,**self.button_primary) 
        self._newcase_data_dir_input = pn.widgets.TextInput(
            label="New Case Data Directory",value="")
        self._newcase_data_dir_printout = pn.pane.Markdown(
            f"Project data will be stored in directory:\n__MUST VALIDATE DATA DIRECTORY__",hard_line_break=True)
        self._newcase_button_data_dir_valid = pn.widgets.Button(
            label="Validate Data Directory",on_click=self._verify_data_dir,**self.button_primary)
        #---
        self._newcase_output_dir_input = pn.widgets.TextInput(
            label="New Case Output Directory",value="")
        self._newcase_output_dir_printout = pn.pane.Markdown(
            f"Project output will be stored in directory:\n__MUST VALIDATE OUTPUT DIRECTORY__",hard_line_break=True)
        self._newcase_button_output_dir_valid = pn.widgets.Button(
            label="Validate Output Directory",on_click=self._verify_output_dir,**self.button_primary)
        self._newcase_button_create = pn.widgets.Button(
            label="Create New Case",on_click=self._click_new_case,**self.button_success)
        self.gv_map = pn.pane.HoloViews(gv.tile_sources.OSM().opts(title="New Case Bounding Box"),width=400,height=400)

        # DELETE CASE WIDGETS
        self._delcase_selector = pn.widgets.Select(options=self._existing_case_selector.options)
        self._delcase_modal = pn.Modal(name="Delete Case",)
        self._delcase_modal_header_text = pn.pane.Markdown("# Are you sure you want to delete this case:")
        self._delcase_modal_case = pn.pane.Markdown("")
        self._delcase_modal_footer_text = pn.pane.Markdown("# This cannot be undone.")
        self._delcase_button_confirm = pn.widgets.Button(label="Delete Case",color='danger',on_click=self._click_del_case)
        self._delcase_button_cancel = self._delcase_modal.create_button("hide",label='Cancel',color='default')
        self._delcase_checkbox = pn.widgets.Checkbox(label='Enable Case Deletion')
        self._delcase_modal_open = self._delcase_modal.create_button("show",label="Delete Case",disabled=True,color='danger')

        # Populate the modal
        self._delcase_modal.append(self._delcase_modal_header_text)
        self._delcase_modal.append(self._delcase_modal_case)
        self._delcase_modal.append(self._delcase_modal_footer_text)
        self._delcase_modal.append(pn.Row(self._delcase_button_cancel,self._delcase_button_confirm))

        # Ensure the modal displays the currently active delcase_selector value
        self._delcase_modal_case.param.update(object = pn.bind(lambda val: f"# `{val}`", self._delcase_selector))

        # Only allow the delete case modal to open if the checkbox is checked
        self._delcase_modal_open.param.update(disabled = pn.bind(lambda val: not val, self._delcase_checkbox))

    def _advance(self,event,dest):
        self.selected_casename = self._existing_case_selector.value
        self.advance_to = dest
        self.ready = True

    def _click_new_case(self,event):
        advance = True

        self._verify_resolution()

        if not self._newcase_resolution_valid:
            msg = "Ensure a valid spatial resolution has been entered."
            advance = False
        if not self._newcase_data_dir_valid:
            msg = "Ensure a valid data directory has been entered with `Validate Data Directory`"
            advance = False

        if not self._newcase_output_dir_valid:
            msg = "Ensure a valid output directory has been entered with `Validate Output Directory`"
            advance = False

        if not self._newcase_bbox_valid:
            msg = "Ensure a valid bounding box has been entered with `Validate Bounding Box`"
            advance = False

        if not advance:
            pn.state.notifications.error(msg)
            return

        _path_data = Path(self._newcase_data_dir_full)
        _path_output = Path(self._newcase_output_dir_full)
        _casename = _path_data.name

        os.makedirs(name=_path_data)
        os.makedirs(name=_path_output)
        self._create_case_registry(_casename,_path_data,_path_output)
        self._update_fhba_cases_json(_casename,_path_data)
        pn.state.notifications.success(f"New Case Created: {_casename}")
        self._existing_case_load_button_disabled = False

        print(f"Updating Case Selector to include : {_casename}")
        self._existing_case_selector.options = [x for x in self._existing_case_selector.options if x is not None]
        self._existing_case_selector.value = _casename
        self._delcase_selector.options = self._existing_case_selector.options
        self._delcase_selector.value = _casename
        print(f"{self._existing_case_selector.options = }")
        print(f"{self._delcase_selector.options = }")

        # WORKAROUND FOR SELECTORS NOT UPDATING AUTOMATICALLY; RELOAD PAGE
        pn.state.location.reload = True
        
    def _click_del_case(self,event):
        _case_to_delete = self._delcase_selector.value
        pn.state.notifications.warning(f"Deleting case: {_case_to_delete}")

        # 1. Delete case directories
        self._json_registry_filename = self._fhba_cases.cases[_case_to_delete] / f"fhba_{_case_to_delete}.json"
        self._json2reg()

        _delcase_caseroot = self._reg.caseroot
        _delcase_outputroot = self._reg.output_root

        print(f"{_delcase_caseroot =}")
        print(f"{_delcase_outputroot =}")

        for _p in [_delcase_caseroot,_delcase_outputroot]:
            if _p.exists() and _p.is_dir():
                shutil.rmtree(_p)

        # 2. Update Case Registry
        del self._fhba_cases.cases[_case_to_delete]
        self._cases2json()
        
        self._existing_case_selector.options = [x for x in self._existing_case_selector.options if x != _case_to_delete]
        self._delcase_selector.options = [x for x in self._existing_case_selector.options if x != _case_to_delete]

        self._delcase_modal.hide()

        # WORKAROUND FOR SELECTORS NOT UPDATING AUTOMATICALLY; RELOAD PAGE
        pn.state.location.reload = True

    def _update_fhba_cases_json(self,casename,path):
        # Update FHBACases object with the new casename:path keyval pair
        self._fhba_cases = self._fhba_cases.model_copy(update={
            "cases":self._fhba_cases.cases | {casename : path}
            })

        cases2json(self._fhba_cases_json, self._fhba_cases)

    def _create_case_registry(self,casename,path_data,path_output):
        from fhba.panel.setup.create_case_registry import create_case_registry
        self._case_registry, self._case_registry_filename = create_case_registry(
            casename=casename,path_data=path_data,
            path_output=path_output,bbox=self._newcase_bbox_input.value,
            dx=self._dx,dy=self._dy
            )
        
    def _show_bbox(self,event):
        self._newcase_bbox_valid, msg = bbox_is_valid(self._newcase_bbox_input.value)
        
        if not self._newcase_bbox_valid:
            self._reset_bbox(event)
            return

        bbox_element = gv.Rectangles([self._newcase_bbox_input.value],crs=ccrs.PlateCarree()).opts(
            fill_alpha=0.2, fill_color="red", line_color="darkred", line_width=2
        )

        self.gv_map.object = (gv.tile_sources.OSM() * bbox_element)

    def _reset_bbox(self,event):
        self._newcase_bbox_valid = False
        self._newcase_bbox_input.value = self._bbox_default
        self.gv_map.object = gv.tile_sources.OSM()

    def _verify_output_dir(self,event):
        output_dir = f"{os.path.join(self._newcase_output_dir_input.value,self._newcase_casename.value)}"
        self._newcase_output_dir_valid, msg = validate_directory(output_dir)

        if not self._newcase_output_dir_valid:
            pn.state.notifications.error(msg)

        else:
            self._newcase_output_dir_full = output_dir
            self._newcase_output_dir_printout.object = f"Project output will be stored in directory:\n__{self._newcase_output_dir_full}__"
            pn.state.notifications.success("Output Directory is Valid.")        

    def _verify_data_dir(self,event):
        data_dir = f"{os.path.join(self._newcase_data_dir_input.value,self._newcase_casename.value)}"
        self._newcase_data_dir_valid, msg = validate_directory(data_dir)

        if not self._newcase_data_dir_valid:
            pn.state.notifications.error(msg)

        else:
            self._newcase_data_dir_full = data_dir
            self._newcase_data_dir_printout.object = f"Project data will be stored in directory:\n__{self._newcase_data_dir_full}__"
            pn.state.notifications.success("Data Directory is Valid.")

    def _verify_resolution(self,event=None):
        self._newcase_resolution_valid = False

        resolution = self._newcase_resolution.value
        print(f"{resolution = }")
        print(f"{len(resolution) = }")
        msg = "Resolution must be one or two integers or floats representing final grid spacing in the x and optionally y directions."
        if (len(resolution) > 0) and (len(resolution) <= 2):
            resolution = [float(x) for x in resolution]
            self._newcase_resolution_valid = True
            self._dx = resolution[0]
            self._dy = resolution[-1] # may be same as dx = resolution[0]

        if not self._newcase_resolution_valid:
            pn.state.notifications.error(msg)

    def _click_load_case(self,event):
        self._advance(event=event,dest="AnalysisPipeline")

    def _json2cases(self):
        self._fhba_cases_json, self._fhba_cases = json2cases()

    def _cases2json(self):
        cases2json(self._fhba_cases_json,self._fhba_cases)

    def _json2reg(self,return_obj=True):
        self._reg = json2reg(self._json_registry_filename)
        if return_obj:
            return self._reg

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        self.ready = False
        self.advance_to = None
        return self._layout
    

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
            ("Instructions",Instructions),
            ("1. Download Granules", self._pipeline_download_layout),
            ("2. Process Granules", self._pipeline_process_layout),
            ("3. Classify Granules", self._pipeline_classify_layout),
            dynamic=True,
            active=1,
        )

        self._layout = pn.Card(pn.Column(
            self._layout_header,
            pn.layout.Divider(),
            self._layout_tabs
            ),header=pn.pane.Markdown(f"# Case: {self.selected_casename}"),**self.card)

    def _setup(self):
        # Navigation Buttons
        self.back    = pn.widgets.Button(name="Back to Case Selection",**self.button_warning)
        self._refresh_json = pn.widgets.Button(name="Refresh Case Info",on_click=self._refresh_case_info,**self.button_success)
        self.back.on_click(lambda e: self._advance("Start"))

        self._json2cases()
        self._json_registry_filename = self._fhba_cases.cases[self.selected_casename] / f"fhba_{self.selected_casename}.json"
        self._json2reg()

        self._json_viewer = pn.widgets.JSONEditor(
            value=self._reg.model_dump(mode='json'),selection=[],mode='view',
        sizing_mode='stretch_width')

        # Analysis Pipelines
        self._build_pipeline_download()
        self._build_pipeline_process()
        self._build_pipeline_classify()

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
            debug=True
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
            debug=True
        )     

        self._pipeline_process_layout = _pipe
        self._pipeline_process = _pipe

    def _build_pipeline_classify(self):
        _pipe = pn.pipeline.Pipeline(
            stages=[
                ('Select',StageSelectInstrument(registry=self._json2reg(return_obj=True),show_classification_selector=True)),
                ('Classify Points',StageClassifyUserpts)
            ],
            debug=True
        )     

        self._pipeline_classify_layout = _pipe
        self._pipeline_classify = _pipe

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