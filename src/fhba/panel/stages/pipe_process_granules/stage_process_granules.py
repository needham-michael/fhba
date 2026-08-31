import sys
import time

import pandas as pd
import panel as pn
import param

from fhba.panel.utils import style
from fhba.reproject import create_target_area_def, reproject_viirs

class StageProcessGranules(param.Parameterized):
 
    year = param.Integer()
    satellite_full = param.String()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()
    sat_band_subset = param.List()
    granules = param.Parameter()
    blend_method = param.String()


    def __init__(self,**params):
        super().__init__(**params)
        self._get_style()
        self._setup()

        selected_band_string = "## Selected Bands:"
        for b in sorted(self.sat_band_subset):
            selected_band_string += f"\n* __`{b}`__"
            if b in self.sat_info.band_list_minimal:
                selected_band_string += " *Required*"

        self._layout = pn.Card(pn.Row(
            pn.Column(
                pn.pane.Markdown(f"## Blending Method:\n {self.blend_method}"),
                pn.pane.Markdown(selected_band_string),
                self._table_layout,   
            ),
            self._download_layout
        ),**self.card)

    def _setup(self):
        self._build_table_pane()
        self._build_processing_pane()      

    def _build_table_pane(self):
        """Build a table showing basic info regarding granule processing
        
        Table has columns for: date, blend_method, and processing status
        """
        date_col = list(self.granules.keys())
        date_col = [d for d in date_col if self.granules[d].is_downloaded]
        blnd_col = [self.granules[d].blend_method for d in date_col]
        proc_col = [self.granules[d].is_processed for d in date_col]

        self._processing_df = pd.DataFrame(data={
            'date':date_col,
            'Blending Method':blnd_col,
            'Processed?':proc_col
        })
        
        self._table = pn.widgets.Tabulator(self._processing_df, height=600, show_index=False)
        self._table_layout = pn.Column(
            pn.pane.Markdown("### Granule Processing Status"),
            self._table
        )

    def _build_processing_pane(self):
        self._process_button = pn.widgets.Button(
            name="Process Granules", **self.button_primary,on_click=self._process_granules)
        self._processing_range_selector = pn.widgets.DateRangePicker(
            name='Processing Range',enabled_dates=list(pd.date_range(
            start=self.valid_min_date, end=self.valid_max_date,freq='D').strftime("%Y-%m-%d")),)
        self._str_out = pn.pane.Str(None, width=400)
        self._progress_bar = pn.widgets.Tqdm()
        self._terminal = pn.widgets.Terminal(options={"cursorBlink": True},width_policy='fit',height=600)
        self._loading_icon = pn.widgets.LoadingSpinner(value=False,size=35)
        self._overwrite_checkbox = pn.widgets.Checkbox(label='Overwrite',value=False)

        self._download_layout = pn.Column(
            pn.pane.Markdown("### Process Granules"),
            self._processing_range_selector,
            pn.Row(self._process_button,self._loading_icon),
            self._overwrite_checkbox,
            self._progress_bar,
            self._str_out,
            pn.pane.Markdown("### Download Log"),
            self._terminal,
            # margin=(10, 10), sizing_mode='stretch_both',styles={'background': '#f0f0f0'}
        )

    def _process_granules(self,event):
        self._loading_icon.value = True
        start_time = time.perf_counter()

        # Redirect stdout to the terminal widget to show processing messages
        sys.stdout = self._terminal
        self._terminal.clear()

        _df = self._processing_df

        if len(_df) == 0:
            pn.state.notifications.warning("No granules to process.")
            self._loading_icon.value = False
            return

        if self._processing_range_selector.value is None:
            _df_dates = pd.to_datetime(_df['date'])
            min_date = _df_dates.min()
            max_date = _df_dates.max()

        else:
            min_date, max_date = pd.to_datetime(self._processing_range_selector.value)

        print(f"Date Range: {min_date} | {max_date}")

        target_area_def = create_target_area_def(
            casename=self.registry.casename,
            bounding_box=self.registry.bounding_box,
            resolution=self.registry.resolution,
            epsg=self.registry.epsg,
            epsg_units=self.registry.epsg_units
        )

        self.registry.epsg_extent = target_area_def.area_extent
        self.registry.to_json()

        for date in self._progress_bar(_df['date'], desc="Processing Granules"):
            if not (min_date <= pd.to_datetime(date) <= max_date):
                self._terminal.write(f"Skipping {date} - outside of selected date range.\n")
                continue

            granule_manager = self.granules[date]
            if granule_manager.is_processed:
                if not self._overwrite_checkbox.value:
                    self._terminal.write(f"Skipping {date} - Already processed. Select 'Overwrite' to Re-Process.\n")
                    continue

            granule_manager = self._process_granules_single_date(date,granule_manager,target_area_def)
            self.granules[date] = granule_manager       

            # Save updated granule to json
            self.registry.to_json()

        end_time = time.perf_counter()
        duration = end_time - start_time
        self._terminal.write(f"Processing Complete in {duration:.6f} seconds.\n")
        
        self._loading_icon.value = False
                        
    def _process_granules_single_date(self,date,granule_manager,target_area_def):
        start_time = time.perf_counter()
        self._terminal.write("="*79 + "\n")
        self._str_out.object = f"Processing granules for date: {date}"
        self._terminal.write(f"{self._str_out.object}\n")
        self._terminal.write(f"Including Bands: {self.sat_band_subset}\n")
        self._terminal.write(f"Blending Method: {self.blend_method}\n")

        reproj_filepath = self.registry.path_processed / f"{self.year}" / f"{self.satellite}"
        reproj_filepath.mkdir(parents=True,exist_ok=True)
        reproj_filename = reproj_filepath / f"{self.registry.casename}_reproj_{self.satellite}_{date.replace("-","")}.nc"

        granule_manager, msg = self._reproject(
            reproj_filename=reproj_filename,
            granule_manager=granule_manager,
            target_area_def=target_area_def,
            blend_method=self.blend_method
            )

        end_time = time.perf_counter()
        duration = end_time - start_time
        self._terminal.write(f"\n{msg}\n")
        self._terminal.write(f"Duration: {duration:.6f} seconds\n")

        return granule_manager

    def _reproject(self,reproj_filename,granule_manager,target_area_def,blend_method):
        if self.sat_info.instrument == 'viirs':
            granule_manager, msg = self._reproject_viirs(
                reproj_filename,granule_manager,target_area_def,blend_method)
        else:
            raise NotImplementedError(f"Processing {self.sat_info.instrument} not implemented.")

        return granule_manager, msg

    def _reproject_viirs(self,reproj_filename,granule_manager,target_area_def,blend_method):

        self._terminal.write(f"Reprojecting Granules...\n")
        self._terminal.write(f"Blending Method {blend_method} Not Yet Implemented\n")
        
        scene = reproject_viirs(
            raw_refl_granule=granule_manager.files.raw_refl_granule,
            raw_cmsk_granule=granule_manager.files.raw_cmsk_granule,
            refl_band_list=self.sat_band_subset,
            cmsk_band_list=['Clear_Sky_Confidence'],
            target_area_def=target_area_def,
            # blend_method=blend_method
        )

        # Satpy expects output filename to be a string
        scene.save_datasets(filename=str(reproj_filename),writer='cf',compute=True)

        granule_manager.files.reproj_granule = reproj_filename
        granule_manager.is_processed = True
        granule_manager.processed_bands = self.sat_band_subset
        msg = f"Success reprojecting {granule_manager}"

        return granule_manager, msg


    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout