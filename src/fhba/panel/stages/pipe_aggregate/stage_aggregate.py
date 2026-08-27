import datetime
import os

from collections import defaultdict
from pathlib import Path

import cartopy.crs as ccrs
import geopandas as gpd
import pandas as pd
import panel as pn
import param
import numpy as np
import rioxarray as rxr
import xarray as xr

from fhba.aggregate import get_burn_area_by_county
from fhba.reproject import write_raster, create_target_area_def
from fhba.viz import MapFigMaker, shp2gdf

from fhba.panel.utils import style, get_valid_dates

class StageAggregate(param.Parameterized):
    year = param.Integer()
    registry = param.Parameter()
    valid_max_date = param.String()
    valid_min_date = param.String()

    def __init__(self,**params):
        super().__init__(**params)
        self._get_style()
        self._setup()
        
        self._layout = pn.Card(
            pn.Row(
                pn.Column(
                    self._widgets,
                    pn.pane.Markdown("## Existing Processed Burnmasks"),
                    self._burnmasks_by_date_tbl
                ),
                pn.Column(
                    self._by_county_tbl_title,
                    self._by_county_tbl
                )
            )
        )

    def _setup(self):
        self._get_valid_dates()
        self._id_burnmasks_by_date()
        self._init_collectors()
    
        self._target_area_def = create_target_area_def(
            casename = self.registry.casename,
            bounding_box = self.registry.bounding_box,
            resolution = self.registry.resolution,
            epsg = self.registry.epsg,
            epsg_units = self.registry.epsg_units
        )

        self._loading_icon = pn.indicators.LoadingSpinner(height=35,value=False)
        self._dateselector = pn.widgets.DateRangePicker(name='Select Date Range',
            start=datetime.date(*[int(x) for x in self.valid_min_date.split("-")]),
            end=datetime.date(*[int(x) for x in self.valid_max_date.split("-")]))
        self._sat_checkbox = pn.widgets.CheckBoxGroup(
            options=list(self.registry.granules[str(self.year)].keys()))
        self._button_aggregate = pn.widgets.Button(
            name="Aggregate Burnmasks",**self.button_primary,on_click=self._aggregate_burnmasks)
        self._progress_bar = pn.widgets.Tqdm()


        self._widgets = pn.WidgetBox(
            self._dateselector,
            self._sat_checkbox,
            self._button_aggregate,
            self._progress_bar,
        )

        self._by_county_tbl_title = pn.pane.Markdown("",hard_line_break=True)
        self._by_county_tbl = pn.widgets.Tabulator(pd.DataFrame(),disabled=True)

    def _id_burnmasks_by_date(self):
        """Build dataframe of finalized burnmasks by satellite and date

        Columns: Satellite Name; Rows: Date
        """
        sat_list = list(self.registry.granules[str(self.year)].keys())
        _burnmasks_by_date = defaultdict(dict)
        for sat in sat_list:
            for date in self.registry.granules[str(self.year)][sat]:
                g = self.registry.granules[str(self.year)][sat][date]
                if g.is_finalized:
                    _burnmasks_by_date[date][sat] = str(g.files.burnmask_final)

        self._burnmasks_by_date = pd.DataFrame(_burnmasks_by_date).T.sort_index()
        self._burnmasks_by_date.index = pd.to_datetime(self._burnmasks_by_date.index)

        # Make a display-able dataframe of True/False to show present/missing burnmasks
        self._burnmasks_by_date_display = ((self._burnmasks_by_date * 0)+"1").fillna(0).astype(bool)
        self._burnmasks_by_date_tbl = pn.widgets.Tabulator(self._burnmasks_by_date_display,disabled=True)

        self._burnmasks_by_date.reset_index().rename(columns={'index':'date'}).to_csv(
            "_tmp_bm_df.csv",index=False)

    def _init_collectors(self):
        self._county_gdf = shp2gdf(self.registry.county_shp)
        self._county_gdf['LABEL'] = self._county_gdf['NAME'] + " (" + self._county_gdf['STATE_ABBR'] + ")"
        
        self._unified_burnmask_df = pd.DataFrame(
            data=[],index=[],columns=['Burnmask','PresentSatellites','AcresBurned'])
        self._acres_burned_by_county = pd.DataFrame(
            data=[],index=[],columns=self._county_gdf['LABEL'])

        try:
            self.registry.processed_burnmasks[str(self.year)]
        except:
            self.registry.processed_burnmasks[str(self.year)] = defaultdict(dict)
        try:
            self.registry.processed_burnmasks_gpkg[str(self.year)]
        except:
            self.registry.processed_burnmasks_gpkg[str(self.year)] = defaultdict(dict)
        try:
            self.registry.processed_burnmasks_csv[str(self.year)]
        except:
            self.registry.processed_burnmasks_csv[str(self.year)] = defaultdict(dict)
        try:
            self.registry.processed_burnmasks_png[str(self.year)]
        except:
            self.registry.processed_burnmasks_png[str(self.year)] = defaultdict(dict)

    def _aggregate_burnmasks(self,event):

        if self._dateselector.value is None:
            pn.state.notifications.warning("Select a Date Range")
            return
        if self._sat_checkbox.value == []:
            pn.state.notifications.warning("Select at least one satellite")
            return

        start_date, end_date = pd.to_datetime(list(self._dateselector.value))
        burnmasks_in_daterange = self._burnmasks_by_date[start_date:end_date]

        # Filter to satellites included in checkbox
        burnmasks_in_daterange = burnmasks_in_daterange[self._sat_checkbox.value]

        if len(burnmasks_in_daterange) == 0:
            pn.state.notifications.warning("No burnmasks found within specified date range.")
            return
        
        d0 = burnmasks_in_daterange.index[0].strftime("%Y-%m-%d")
        for d in burnmasks_in_daterange.index.strftime("%Y-%m-%d"):
            self._get_burnmask_from_daterange(
                start_date=d0,end_date=d,burnmasks_in_daterange=burnmasks_in_daterange)

        pn.state.notifications.info("Aggregation Complete.")

    def _get_burnmask_from_daterange(self,start_date,end_date,burnmasks_in_daterange):

        # Filter to date range subset
        subset_burnmasks = burnmasks_in_daterange[start_date:end_date].dropna(axis=1,how='all')
        date_range = (subset_burnmasks.index[0], subset_burnmasks.index[-1])
        date_range_str = f"{date_range[0].strftime('%Y%m%d')}-{date_range[1].strftime('%Y%m%d')}"
        date_range_str_display = f"{date_range[0].strftime('%b %d')} - {date_range[1].strftime('%b %d, %Y')}"
        
        # Require at least two dates for aggregation
        if date_range[0] == date_range[1]:
            pn.state.notifications.info(f"Skipping First Date: {date_range[0]}")
            return 

        present_satellites = list(subset_burnmasks.columns)
        present_satellites_short_name = "-".join(
            [self.registry.sat_info[sat].abbreviation for sat in present_satellites])

        self._prep_burnmask_containers(sat_combo=present_satellites_short_name)
        
        burnmask = xr.concat(
            [rxr.open_rasterio(x).squeeze() for x in subset_burnmasks.to_numpy().flatten() if type(x) == str],
            dim='bm')

        # Require a pixel to be burned at least TWICE to be counted
        burnmask = (burnmask.sum(dim='bm') >= 2).astype(int)

        output_dir = self.registry.path_burnmask_seasonal / f"{self.year}" / present_satellites_short_name
        output_filename_tif = output_dir / f"{self.registry.casename}_unified_burnmask_{date_range_str}.tif"
        output_filename_gpkg = str(output_filename_tif).replace(".tif",".gpkg")
        output_filename_csv = str(output_filename_tif).replace(".tif",".csv")
        output_filename_png = str(output_filename_tif).replace(".tif",".png")
        output_filename_tif.parent.mkdir(parents=True,exist_ok=True)

        write_raster(
            raster = burnmask,
            output_filename = output_filename_tif,
            target_area_def = self._target_area_def,
            dtype='int8'
        )

        burn_area_by_county = get_burn_area_by_county(
            burnmask_file = output_filename_tif,
            county_shp = self.registry.county_shp
        )

        # Write burned area by county geodataframe to to GPKG (for GIS) and csv formats
        burn_area_by_county.to_file(output_filename_gpkg,driver='GPKG')
        burn_area_by_county.drop(columns='geometry').to_csv(output_filename_csv,index=False)

        # Update display table
        total = float(burn_area_by_county['burned_area_acres'].sum())
        self._by_county_tbl_title.object = f"## {total:,.0f} Acres Burned {date_range_str_display}\n__Based on Imagery from Satellites:__"
        for sat in present_satellites:
            self._by_county_tbl_title.object += f"\n * {sat}"
        self._by_county_tbl.value = burn_area_by_county.drop(columns='geometry')

        MapFigMaker(
            date_range = date_range_str,
            sat_combo = present_satellites_short_name,
            fname_tif = output_filename_tif,
            crs = ccrs.epsg(self.registry.epsg),
            fig_title = f"{self.registry.casename.capitalize()} Acreage Burned ({start_date} - {end_date})",
            county_shp = self.registry.county_shp
        ).make_figure()

        self.png = output_filename_png

        # Update registry
        self.registry.processed_burnmasks[str(self.year)][present_satellites_short_name][date_range_str] = output_filename_tif
        self.registry.processed_burnmasks_gpkg[str(self.year)][present_satellites_short_name][date_range_str] = output_filename_gpkg
        self.registry.processed_burnmasks_csv[str(self.year)][present_satellites_short_name][date_range_str] = output_filename_csv
        self.registry.processed_burnmasks_png[str(self.year)][present_satellites_short_name][date_range_str] = output_filename_png
        self.registry.to_json()

    def _prep_burnmask_containers(self,sat_combo):
        try:
            self.registry.processed_burnmasks[str(self.year)][sat_combo]
        except:
            self.registry.processed_burnmasks[str(self.year)][sat_combo] = {}
        try:
            self.registry.processed_burnmasks_gpkg[str(self.year)][sat_combo]
        except:
            self.registry.processed_burnmasks_gpkg[str(self.year)][sat_combo] = {}
        try:
            self.registry.processed_burnmasks_csv[str(self.year)][sat_combo]
        except:
            self.registry.processed_burnmasks_csv[str(self.year)][sat_combo] = {}
        try:
            self.registry.processed_burnmasks_png[str(self.year)][sat_combo]
        except:
            self.registry.processed_burnmasks_png[str(self.year)][sat_combo] = {}

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def _get_valid_dates(self):
        self.valid_min_date, self.valid_max_date = get_valid_dates(year=self.year)

    def panel(self):
        return self._layout