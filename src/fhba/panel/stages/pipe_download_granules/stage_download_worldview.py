from datetime import datetime

import pandas as pd
import panel as pn
import param

from fhba.schemas import GranuleManager, FileMetadata
from fhba.panel.utils import style
from fhba.download.worldview import download_worldview

class StageDownloadWorldview(param.Parameterized):
    """Download and preview TrueColor images from NASA Worldview"""

    year = param.Integer()
    satellite_full = param.String()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()

    def __init__(self,**params):
        super().__init__(**params)
        self._get_style()
        self._setup()

        self._layout = pn.Card(pn.Column(
            self._date_range_slider,
            pn.Row(self._download_image_button,self._loading_icon),
            self._pbar,
            self._png_label,
            self._png_pane
        ),title=f"Satellite: {self.satellite_full}; Year: {self.year}",**self.card)

    def _setup(self):
        self._default_start_date = pd.to_datetime(f"{self.year}-02-15")
        self._default_end_date = pd.to_datetime(f"{self.year}-05-15")
        self._today = pd.to_datetime(datetime.now())

        if self._default_end_date > self._today:
            self._default_end_date = self._today

        if self._default_start_date > self._today:
            self._default_start_date = pd.to_datetime(self.valid_min_date)

        _value = (self._default_start_date,self._default_end_date)

        self._download_image_button = pn.widgets.Button(
            name="Download Preview Images",on_click=self._download_images,**self.button_primary)
        self._date_range_slider = pn.widgets.DateRangeSlider(
            start=pd.to_datetime(self.valid_min_date),end=pd.to_datetime(self.valid_max_date),
            name="Date Range",format='%Y-%m-%d',value=_value)
        self._loading_icon = pn.widgets.LoadingSpinner(value=False,size=35)
        
        self._pbar = pn.widgets.Tqdm()
        self._png_pane = pn.pane.PNG(None,width=400,height=400,fixed_aspect=True)
        self._png_label = pn.pane.Markdown("Preview Image for Date:",width=400)  

    def _download_images(self,event):
        date_range = pd.date_range(
            self._date_range_slider.value[0], self._date_range_slider.value[1],freq='1D')

        self._loading_icon.value = True

        for date in self._pbar(date_range,desc="Downloading Preview Images"):

            date = date.strftime("%Y-%m-%d")

            truecolor_img_path = self._get_worldview_filename(date)

            download_valid, truecolor_img_path = download_worldview(
                date=date,bbox=self.registry.bounding_box,overwrite=False,
                satellite_name=self.satellite,out_path=truecolor_img_path
            )

            if download_valid:
                self._png_pane.object = truecolor_img_path
                self._png_label.object = f"Preview Image for Date: {date}"

                self._initialize_granule_manager(date,truecolor_img_path)

        self._loading_icon.value = False

    def _get_worldview_filename(self,date):
        filename = self.registry.path_wldv / str(self.year) / self.satellite
        filename = filename / f"truecolor_{self.registry.casename}_{date}.png"
        filename.parent.mkdir(parents=True,exist_ok=True)
        return str(filename)

    def _initialize_granule_manager(self,date,truecolor_img_path):
        gm = GranuleManager(
            date=date,
            satellite=self.satellite_full,
            files = FileMetadata(
                truecolor_img_path = truecolor_img_path
            )
        )

        # Assign to proper location within registry
        granules = self.registry.granules

        if str(self.year) not in granules:
            granules[str(self.year)] = {}

        if self.satellite_full not in granules[str(self.year)]:
            granules[str(self.year)][self.satellite_full] = {}

        granules[str(self.year)][self.satellite_full][date] = gm

        # Update the registry and serialize to json
        self.registry.granules = granules
        self.registry.to_json()
        
    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout