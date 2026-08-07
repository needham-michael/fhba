import datetime
import os

import geopandas as gpd
import pandas as pd
import panel as pn
import param

from fhba.app.utils import get_instructions
from fhba.aggregate_burnmasks import SatelliteBurnmask, UnifiedBurnmask, get_burn_area_by_county
from fhba.generate_burnmask_figure import generate_burnmask_figure

class StageAggregate(param.Parameterized):

    year = param.Integer()
    registry = param.Parameter()

    def get_widgets(self):

        generate_button = pn.widgets.Button(
            name="Generate Seasonal Burn Map",
            button_type="primary",
            width=220,
            # disabled=True
        )

        generate_all_button = pn.widgets.Button(
            name="Generate All YTD Burn Maps",
            button_type="primary",
            width=220,
            # disabled=True
        )

        download_stats_button = pn.widgets.FileDownload(
            label="Download Statistics CSV",
            button_type="success",
            width=220,
            visible=False
        )

        loading = pn.indicators.LoadingSpinner(
            name="", width=200, height=50,
            visible=False, value=False
        )

        daterange_selector = pn.widgets.DateRangePicker(
            name='AggregateBurnmask Date Range',
            enabled_dates=list(pd.date_range(
                start=f"{self.year}-01-01", 
                end=f"{self.year}-12-31",
                freq='D'
            ).strftime("%Y-%m-%d")),
        )

        satellite_options = list(self.registry[self.year].satellites.keys())

        satellite_checkbox = pn.widgets.CheckBoxGroup(
            options=satellite_options,
            value=satellite_options
        )

        progress_bar = pn.widgets.Tqdm()

        return generate_button, generate_all_button, download_stats_button, loading,daterange_selector, satellite_checkbox, progress_bar

    @param.depends('year', 'registry',)
    def view(self):
        instr = get_instructions("08_instr_aggregate.md", instr_width=250)
        instr.objects += [pn.pane.Alert(
                    "Aggregation unions all finalized burn masks from selected satllites for this year"
                    "into a single YTD seasonal map.",
                    alert_type='info',width=250)]
        
        generate_button, generate_all_button, download_stats_button, loading,daterange_selector, satellite_checkbox, progress_bar = self.get_widgets()


        stats_table = pn.pane.DataFrame(pd.DataFrame(), index=False, width=600)
        stats_table_heading = pn.pane.Markdown(f"")
        
        proj = self.registry.satpy_area_def.to_cartopy_crs()
        county_shp = self.registry.county_shp + ".shp"
        county_gdf = gpd.read_file(county_shp)
        county_gdf = county_gdf.to_crs(proj)
        

        product_convention = {
            'Suomi-NPP':'VNP',
            'NOAA-20':'VJ1',
            'NOAA-21':'VJ2'
        }

        classification_methods = ['eucl','rf','svm']

        def generate_burnmask_ytd(event):

            output_dir = os.sep.join(self.registry.raw_data_dir.split(os.sep)[:-1])
            output_dir = os.sep.join([output_dir,"final_burnmask"])
            output_dir = os.sep.join([output_dir,str(self.year)])
            os.makedirs(output_dir,exist_ok=True)

            loading.value = True

            filename = f"viirs_burnmask_{self.year}"

            burnmasks = {}

            start_date, end_date = pd.to_datetime(daterange_selector.value)

            # ytd = pd.to_datetime(daterange_selector.value)
            
            # print("="*79)

            # if isinstance(ytd,type(pd.NaT)):
                # ytd = None

            # print(f"2. {ytd = }")

            for satellite in satellite_checkbox.value:
                try:
                    bm = SatelliteBurnmask(satellite=satellite,year=self.year,classification_methods=classification_methods)
                    bm.merge_burnmasks(verbose=False,method='majority',min_date=start_date,max_date=end_date)
                    burnmasks[satellite] = bm

                    filename += f"_{product_convention[satellite]}"
                except:
                    # Skip when a satellite has not had a burn mask YTD, occurs
                    # early in the season
                    pass
                
            burnmask = UnifiedBurnmask(
                burnmasks = burnmasks,
                year = self.year,
                classification_methods=classification_methods
            )

            # if end_date is None:
            #     ytd = pd.to_datetime(burnmask.burnmask_dates[-1])

            valid_dates = [x for x in pd.to_datetime(burnmask.burnmask_dates) if x <= end_date]
            valid_dates = [x for x in valid_dates if x >= start_date]
            start_date_str = valid_dates[0].strftime("%Y%j")
            end_date_str = valid_dates[-1].strftime("%Y%j")
            
            filename += f"_{start_date_str}-{end_date_str}.tif"
            filename = os.sep.join([output_dir,filename])

            burnmask.join_burnmasks(method='any')
            burnmask.write_burnmask(filename,overwrite=True)

            table = get_burn_area_by_county(filename,county_shp=county_shp)
            table.to_csv(filename.replace(".tif",".csv"),index=False)
            table = table[['county_name','state_name','burned_area_acres']]
            table = table.rename(columns={'county_name':'County','state_name':'State','burned_area_acres':'Acres Burned'})
            stats_table.object = table
            stats_table_heading.object = f"### Burned Area by County ({start_date.strftime("%Y-%m-%d")} through {end_date.strftime("%Y-%m-%d")})"

            generate_burnmask_figure(
                date = end_date.strftime("%Y-%m-%d"),
                initial_date = valid_dates[0],
                filename = filename,
                annotation = filename.split(os.sep)[-1].split(".tif")[0].replace("_","-"),
                proj = proj,
                county_gdf=county_gdf
            )

            loading.value = False

        def generate_all_burnmask_ytd(event):

            # Build a list of all possible dates with calculated burn masks
            valid_dates = []
            for sat in self.registry[self.year].satellites.keys():
                if sat in satellite_checkbox.value:
                    gm = self.registry[self.year][sat]

                    for method in list(gm.burnmask_by_date):

                        for date in gm.burnmask_by_date[method]:
                            try:
                                # Backwards compatible to verify that dates are being
                                # used as keys
                                pd.to_datetime(date)
                                valid_dates.append(date)
                            except Exception:
                                pass

            # Convert to a set to remove duplicate values then back to a sorted list
            valid_dates =  sorted(list(set(valid_dates)))

            # Ensure that dates are within the bounds of the daterange_selector
            d1, d2 = pd.to_datetime(daterange_selector.value)
            valid_dates = [x for x in valid_dates if (pd.to_datetime(x) >= d1) and (pd.to_datetime(x) <= d2)]

            for date in progress_bar(valid_dates):

                daterange_selector.value = (
                    daterange_selector.value[0],
                    datetime.date(*[int(x) for x in date.split("-")])
                    )

                try:
                    generate_burnmask_ytd(event)
                except ValueError:
                    pass


        generate_button.on_click(generate_burnmask_ytd)

        generate_all_button.on_click(generate_all_burnmask_ytd)

        pane = pn.Row(
            instr,
            pn.Column(
                pn.pane.Markdown("## Seasonal Burn Scar Aggregation"),
                pn.layout.Divider(),
                pn.Column(
                    pn.Column(
                        pn.pane.Markdown("#### Select Satellite(s) for Analysis (Only Analyzed Satellites Shown)"),
                        satellite_checkbox
                    ),
                    pn.Row(daterange_selector,),
                    pn.Row(
                        # generate_button, # Hide this button since expect user to look at entire season
                        pn.Column(generate_all_button,progress_bar)
                        ),
                ),
                loading,
                stats_table_heading,
                stats_table,
                download_stats_button,
                margin=(40, 10), sizing_mode='stretch_both',
                styles={'background': '#f0f0f0'}
            )
        )

        return pane
    
    def panel(self):
        return pn.Row(self.view,)
