import os
import sys 
import time

import earthaccess
import pandas as pd
import panel as pn
import param

from earthaccess.exceptions import LoginAttemptFailure, LoginStrategyUnavailable

from fhba.panel.utils import style
from fhba.download.earthaccess import earthaccess_search_granules

class StageDownloadGranules(param.Parameterized):
    """Pipeline stage to download granules from earthaccess"""
    year = param.Integer()
    satellite_full = param.String()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()
    granules = param.Parameter()

    def __init__(self,**params):
        super().__init__(**params)
        self._get_style()
        self._setup()

        if self._show_alert:
            self._layout = pn.pane.Alert(
                f"## Need to categorize preprocessed true color images for {self.satellite_full} during {self.year}",alert_type='warning')
            return

        self._layout = pn.Card(pn.Row(
            pn.Column(
                self._auth_layout,
                pn.layout.Divider(),pn.layout.Divider(),
                self._table_layout,
            ),
            self._download_layout
        ),**self.card)

    def _setup(self):
        self._show_alert = False
        try:
            self._build_table_pane()
            self._build_authenticate_pane()
            self._build_download_pane()
        except:
            self._show_alert = True

        self._ea_local_path = self.registry.path_raw / f"{self.year}" / f"{self.satellite}"
        self._ea_local_path.mkdir(exist_ok=True,parents=True)

    def _auth_earthaccess(self,event):
        sys.stdout = self._terminal
        
        password = self._password_box.value
        username = self._username_box.value

        self._auth_loading.value = True
        self._auth_loading.visible = True

        os.environ['EARTHDATA_USERNAME'] = username
        os.environ['EARTHDATA_PASSWORD'] = password

        
        self._ea_login_success = False
        try:
            _ea_login = earthaccess.login(strategy='environment')
            self._ea_login_success = _ea_login.authenticated
        except LoginAttemptFailure:
            pn.state.notifications.error("ERROR AUTHENTICATING")
        except LoginStrategyUnavailable:
            pn.state.notifications.error("Re-enter Credentials.")

        self._auth_loading.value = False
        self._auth_loading.visible = False
        if self._ea_login_success:
            self._auth_earthaccess_indicator.visible = True
            pn.state.notifications.success("Success!")
            print("="*30+"\n  Earthaccess Authenticated.  \n"+"="*30)

        # Return stdout to normal and show completion message
        sys.stdout = sys.__stdout__


    def _build_table_pane(self):
        """Build a table showing basic info regarding granule downloads
        
        Table has columns for  date, categorization, and download status
        """
        date_col = list(self.granules.keys())
        cat_col = [self.granules[d].categorization for d in date_col]
        dl_col = [self.granules[d].is_downloaded for d in date_col]

        self._download_df = pd.DataFrame(data={
            'date':date_col,
            'user_categorization':cat_col,
            'download_status':dl_col
        })

        by_cat = self._download_df.groupby('user_categorization').count().iloc[:,0]

        self._table = pn.widgets.Tabulator(self._download_df, height=600, show_index=False)
        self._table_layout = pn.Column(
            pn.pane.Markdown("### Granule Download Status by User Categorization"),
            pn.pane.Str(by_cat.to_string()),
            self._table
        )

    def _build_authenticate_pane(self):
        self._password_box = pn.widgets.PasswordInput(name='password')
        self._username_box = pn.widgets.TextInput(name='username')
        self._auth_earthaccess_button = pn.widgets.Button(
            name="Authenticate Earthdata",**self.button_primary,on_click=self._auth_earthaccess)
        self._auth_loading = pn.indicators.LoadingSpinner(
            name='Authenticating Earthaccess...',visible=False,size=20)      
        self._auth_earthaccess_indicator = pn.pane.Str(
            "Authenticated.",visible=False,styles={'color':'#009E73','font-weight':'bold'},)

        self._auth_layout = pn.Column(
            pn.pane.Markdown("### Authenticate with NASA Earthdata Service"),
            self._username_box,
            self._password_box,
            pn.Row(self._auth_earthaccess_button,self._auth_earthaccess_indicator,self._auth_loading),
        )

    def _build_download_pane(self):
        self._download_button = pn.widgets.Button(
            name="Download Granules", **self.button_primary,on_click=self._download_granules)
        self._checkbox_group = pn.widgets.CheckBoxGroup(
            name="Select User Categorizations to Download Granules For",
            options=["Fully Clear", "Mostly Clear", "Mostly Cloudy", "Fully Cloudy", "Uncategorized"],
            inline=True,value=["Fully Clear"])
        self._download_range_selector = pn.widgets.DateRangePicker(
            name='Download Range',enabled_dates=list(pd.date_range(
            start=self.valid_min_date, end=self.valid_max_date,freq='D').strftime("%Y-%m-%d")),)
        self._str_out = pn.pane.Str(None, width=400)
        self._progress_bar = pn.widgets.Tqdm()
        self._terminal = pn.widgets.Terminal(options={"cursorBlink": True},width_policy='fit',height=600)
        self._loading_icon = pn.widgets.LoadingSpinner(value=False,size=35)
    
        self._download_layout = pn.Column(
            pn.pane.Markdown("### Download Granules for Selected User Categorizations"),
            self._checkbox_group,
            self._download_range_selector,
            pn.Row(self._download_button,self._loading_icon),
            self._progress_bar,
            self._str_out,
            pn.pane.Markdown("### Download Log"),
            self._terminal,
            # margin=(10, 10), sizing_mode='stretch_both',styles={'background': '#f0f0f0'}
        )

    def _download_granules(self,event):
        start_time = time.perf_counter()
        self._loading_icon.value = True
        if not self._auth_earthaccess_indicator.visible:
            pn.state.notifications.warning("Please authenticate with NASA Earthdata first.")
            self._loading_icon.value = False
            return

        # Redirect stdout to the terminal widget to show download messages
        sys.stdout = self._terminal
        self._terminal.clear()

        _df = self._download_df[self._download_df['user_categorization'].isin(self._checkbox_group.value)]

        if len(_df) == 0:
            pn.state.notifications.warning("No granules to download for selected categories.")
            self._loading_icon.value = False
            return

        if self._download_range_selector.value is None:
            _df_dates = pd.to_datetime(_df['date'])
            min_date = _df_dates.min()
            max_date = _df_dates.max()

        else:
            min_date, max_date = pd.to_datetime(self._download_range_selector.value)

        print(f"Date Range: {min_date} | {max_date}")

        for date in self._progress_bar(_df['date'], desc="Downloading Granules"):
            if not (min_date <= pd.to_datetime(date) <= max_date):
                self._terminal.write(f"Skipping {date} - outside of selected date range.\n")
                continue

            granule_manager = self.granules[date]
            if not granule_manager.is_downloaded:
                granule_manager = self._download_granules_single_date(date,granule_manager)
                self.granules[date] = granule_manager

                # Save updated granule to json
                self.registry.to_json()
            else: 
                print(f"Granules already downloaded for {date}. Skipping.")

        end_time = time.perf_counter()
        duration = end_time - start_time
        self._terminal.write("="*79 + "\n")   
        self._terminal.write(f"Download Complete in {duration:.6f} seconds.\n")
        self._terminal.write("="*79 + "\n")   
        self._loading_icon.value = False


    def _download_granules_single_date(self,date,granule_manager):
        start_time = time.perf_counter()
        self._terminal.write("="*79 + "\n")
        self._str_out.object = f"Downloading granules for date: {date}"
        
        msg, granule_manager = self._prepare_data(date=date,granule_manager=granule_manager)

        end_time = time.perf_counter()
        duration = end_time - start_time
        self._terminal.write(f"\n{msg} {duration:.6f} seconds\n")

        return granule_manager

    def _prepare_data(self,date,granule_manager):
        """Prepare data for a given date by downloading and preprocessing granules."""

        if self.sat_info.access_method == 'earthaccess':
            msg, granule_manager = self._prepare_data_earthaccess(date, granule_manager)
        else:
            raise NotImplementedError("Only `earthaccess` method currently implemented")

        return msg, granule_manager

    def _prepare_data_earthaccess(self,date, granule_manager):
        granules_refl = earthaccess_search_granules(
            date=date,bounding_box=self.registry.bounding_box,sat_info=self.sat_info
        )
        
        granules_cmsk = earthaccess_search_granules(
            date=date,bounding_box=self.registry.bounding_box,sat_info=self.sat_info,
            search_cloudmask = True
        )  

        n_refl = len(granules_refl)
        n_cmsk = len(granules_cmsk)      

        if n_refl == 0:
            msg = f"No reflectance granules identified for {date}. Marking as unavailable."
            granule_manager.is_unavailable = True

        print(f"Identified {n_refl} reflectance granules.")
        print(f"Identified {n_cmsk} cloud mask granules.")

        try:
            print(f"Downloading {n_refl + n_cmsk} files to:\n  {self._ea_local_path}")
            granule_files = earthaccess.download(
                granules_refl + granules_cmsk,
                local_path = self._ea_local_path
            )
            msg = f"{n_refl + n_cmsk} granules successfully downloaded."

        except Exception:
            msg = f"Error downloading files for {date}. Marking as unavailable."
            granule_manager.is_unavailable = True
            return msg, granule_manager

        raw_cmsk_granule = []
        for f in granule_files:
            for short_name in self.sat_info.cmsk_short_name_list:
                if short_name in str(f):
                    raw_cmsk_granule.append(f)
        raw_refl_granule = [p for p in granule_files if p not in raw_cmsk_granule]

        granule_manager.files.raw_cmsk_granule = raw_cmsk_granule
        granule_manager.files.raw_refl_granule = raw_refl_granule
        granule_manager.is_unavailable = False
        granule_manager.is_downloaded = True
        granule_manager.is_retained = True

        return msg, granule_manager

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout
