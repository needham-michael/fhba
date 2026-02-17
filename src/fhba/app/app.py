import importlib
import os
from datetime import datetime

import holoviews as hv
import geoviews as gv
import geopandas as gpd
import numpy as np
import pandas as pd
import panel as pn
import param

from fhba.registry import Registry
from fhba.app.hv_rgb import get_nir_red_img, load_scene

pn.extension('tabulator')
pn.extension(notifications=True)

instr_width = 250
component_width = 800

class StageSetup(param.Parameterized):

    year = param.Selector(default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1)))
    satellite_full = param.Selector(default='Suomi-NPP VIIRS', objects=['Suomi-NPP VIIRS', 'NOAA-20 VIIRS', 'NOAA-21 VIIRS', 'AQUA MODIS', 'TERRA MODIS'])

    @param.output(('satellite',param.String),('registry',param.Parameter),('gm',param.Parameter))
    def output(self):

        # Skip getting the satpy_area_def since no processing occurs in this portion
        # of the application. Also skip authentication with earthaccess for a similar

        registry = Registry(get_satpy_area_def=False,auth_earthaccess=False).load_json()

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

        instr = get_instructions("stage1.md")

        pane = pn.Row(
            instr,
            pn.Column(
            pn.pane.Markdown("## Select Analysis Year and Satellite Instrument"),
            self.param.year,
            self.param.satellite_full,
            margin=(40, 10), width=component_width,styles={'background': '#f0f0f0'}
        ))
        
        return pane

    def panel(self):
        return pn.Row(self.view,)
        
class StagePreview(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('registry','gm')
    def img_pane(self):

        img_path = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"
        img_pane = pn.pane.Image(img_path,width=400,height=800,visible=True)

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index':'date'})
        gm_df = gm_df[gm_df['download_status'] == True]

        preview_image_player = pn.widgets.DiscretePlayer(
            name="Date", 
            options=gm_df['date'].tolist(), 
            width=400,
            visible_buttons=['first', 'previous', 'next', 'last'],
            visible_loop_options=[],
        )

        user_categorization_categories = [
            "Fully Cloudy", "Mostly Cloudy", "Mostly Clear", "Fully Clear", "Uncategorized"
        ]

        user_categorization_selector = {}
        for date, cat in zip(gm_df['date'], gm_df['user_categorization']):
            user_categorization_selector[date] = pn.widgets.Select(
                name=f"User Categorization for Date: {str(date)}", 
                options=user_categorization_categories, 
                value=cat,
                width=400,
            )

        def update_image(event):
            date = preview_image_player.value
            img_path = self.gm.truecolor_images_by_date.get(date, None)
            if img_path is not None and os.path.exists(img_path):
                img_pane.object = img_path
            else:
                img_pane.object = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"

        preview_image_player.param.watch(update_image,"value")
        empty = pn.Spacer(height=0)
        current_categorization_selector = pn.bind(
            lambda date: user_categorization_selector.get(date,empty), 
            preview_image_player
        )
        
        save_categorization_button = pn.widgets.Button(
            name="Save User Categorizations", button_type="primary", width=400
            )
        
        def save_categorization(event):
            for date, selector in user_categorization_selector.items():
                category = selector.value
                self.gm.update_user_categorization(date, category)

            self.registry.save_json()
            pn.state.notifications.success("Categorizations Saved Successfully!")

        save_categorization_button.on_click(save_categorization)        

        return pn.Column(pn.pane.Markdown("## True Color Image Previews"),
                preview_image_player,
                current_categorization_selector,
                save_categorization_button,
                img_pane,)


    @param.depends('year','satellite','registry')
    def view(self):
        instr = get_instructions("stage2.md")
        img_pane = self.img_pane()

        pane = pn.Row(
            instr,
            pn.Column(
                img_pane,
                margin=(40, 10), width=component_width,styles={'background': '#f0f0f0'}
            )
        )
        
        return pane

    def panel(self):
        return pn.Row(self.view,)
    
class StageAnalyze(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year','satellite','registry', 'gm')
    def table_pane(self,return_df=False):

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index':'date'})
        gm_df = gm_df[gm_df['download_status'] == True]
        
        table = pn.pane.DataFrame(
            gm_df[['date','user_categorization','analysis_status']], index=False, width=600
        )

        if return_df:
            return table, gm_df
        
        return table

    @param.depends('year','satellite','registry', 'gm')
    def view(self):
        instr = get_instructions("stage3.md")

        table, gm_df = self.table_pane(return_df=True)

        analysis_date_selector = pn.widgets.Select(
            name="",
            options=gm_df['date'].tolist(),
            width=200,
        )

        load_img_button = pn.widgets.Button(
            name="Load Analysis Date Image", button_type="primary", width=200
        )

        # NOTE: The image loading and analysis functionality is not yet implemented. The button click will simply trigger a notification for now.
        # FOR 2/12/2026 NEED TO IMPLEMENT: On button click, load the image for the selected analysis date and display it in a new pane below the table. This will likely involve using the load_scene and get_nir_red_img functions from hv_rgb.py to load the appropriate granule, preprocess it, and generate a preview image for analysis.   
        # THIS SHOULD UTILIZE THE load_scene and get_nir_red_img functions from hv_rgb.py to load the appropriate granule, preprocess it, and generate a preview image for analysis. The image pane should be displayed below the table and should update based on the selected analysis date when the button is clicked.
        
        county_overly = hv.Path(gpd.read_file(self.gm.county_shp + ".shp").geometry).opts(
            color='white', line_width=0.75
            )


        # hv_rgb = self.gm.get_nir_red_hv_rgb(date=analysis_date_selector.value)
        hv_pane = pn.pane.HoloViews(county_overly, width=500, height=1000)

        loading = pn.indicators.LoadingSpinner(name="Loading Image...", width=200, height=50,visible=False,value=False)

        points_burned, points_burned_stream = initialize_userpoints(color='red')
        points_unburned, points_unburned_stream = initialize_userpoints(color='white',marker='+')

        def update_hv_rgb(event):
            date = analysis_date_selector.value

            print(f"Updating HV RGB with {date = }")  # Debugging statement
            
            loading.value = True
            loading.visible = True
            loading.name='Loading Image...'

            try:
                rgb = self.gm.get_nir_red_hv_rgb(date=date,in_app=True)
            except Exception as e:
                pn.state.notifications.error(f"Error loading image for date {date}: {str(e)}",duration=6000)
                loading.value = False
                loading.name='Error Loading Image.'
            
            if isinstance(rgb, str):
                pn.state.notifications.error(rgb)
                return
            
            if rgb is not None:
                hv_pane.object = rgb * county_overly * points_burned * points_unburned 
            loading.value = False
            loading.visible = False
        
        load_img_button.on_click(
            lambda event: pn.state.notifications.info(f"Loading image for analysis date: {analysis_date_selector.value}")
        )

        load_img_button.on_click(update_hv_rgb)

        export_button = pn.widgets.Button(
            name='Export Points', button_type='primary')

        export_options = pn.widgets.CheckBoxGroup(
            name='Export Options', 
            value=[], 
            options=['Overwrite Points']
        )

        def export_button_callback(event):
            selected_options = export_options.value
            date = analysis_date_selector.value

            points_csv_file = os.path.join(
                self.gm.userpts_dir,f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )

            export_df = pts2df(
                burned_pts = points_burned_stream.data,
                unburned_pts = points_unburned_stream.data
            )

            if self.gm.userpts_dir is None:
                self.gm.userpts_dir = "./tmp_userpts"


            if export_df.empty:
                pn.state.notifications.error('No Points Have Been Selected For This Granule.',duration=3000)

                return
            
            os.makedirs(self.gm.userpts_dir,exist_ok=True)

            date = analysis_date_selector.value

            points_csv_file = os.path.join(
                self.gm.userpts_dir,f"./{self.gm.spatial_name}_userpts_{self.gm.satellite_name}_{date}.csv"
            )
            
            proceed = True
            if os.path.exists(points_csv_file):
                proceed = False
                if 'Overwrite Points' in selected_options:
                    proceed = True

            if proceed:
                export_df.to_csv(points_csv_file,index=False)
                pn.state.notifications.success('Points exported to file.',duration=3000)

                n_burned = len(export_df[export_df['isBurned']==1])
                n_unburned = len(export_df[export_df['isBurned']==0])
                self.gm.update_analysis_status(date, f'{n_burned} burned points, {n_unburned} unburned points')
                self.registry.save_json()

            else:
                pn.state.notifications.error('Points Have Already Been Exported For This Granule. Select Overwrite Points To Ignore.',duration=3000)

                return
            
        def reload_table(event):
            table.object = self.table_pane(return_df=False).object
            
        export_button.on_click(export_button_callback)
        export_button.on_click(reload_table)

            
        return pn.Row(
            instr,
            pn.Column(
                pn.pane.Markdown("## Select Analysis Date from Table of Downloaded Granules"),
                pn.Row(analysis_date_selector, load_img_button),
                pn.layout.Divider(),
                table,
            ),
            pn.Column(
                pn.pane.Markdown("## Identify Burned and Unburned Pixels"),
                pn.Column(loading,pn.Row(export_button,export_options),hv_pane)),
            margin=(40, 10), styles={'background': '#f0f0f0'})

    def panel(self):
        return pn.Row(self.view,)

def get_instructions(filename,instr_width=instr_width):

    instructions = importlib.resources.open_text(
        'fhba.app.appdata.instructions', filename
    ).readlines()

    return pn.pane.Markdown("".join(instructions),width=instr_width)

def initialize_userpoints(color,marker='x'):

    # Empty data to hold point locations
    point_locations = ([], [],)
    points = hv.Points(point_locations).opts(color=color,marker=marker,size=20,)
    point_stream = hv.streams.PointDraw(data=points.columns(), source=points)

    userpoints = points.opts(active_tools=['point_draw'])

    return userpoints, point_stream

def pts2df(burned_pts,unburned_pts):

    burned_df = pd.DataFrame(burned_pts)
    burned_df['isBurned'] = [1 for x in range(burned_df.shape[0])]
    unburned_df = pd.DataFrame(unburned_pts)
    unburned_df['isBurned'] = [0 for x in range(unburned_df.shape[0])]
    
    export_df = pd.concat([burned_df,unburned_df])

    return export_df

def build_app():
    
    pipeline = pn.pipeline.Pipeline(debug=True)
    pipeline.add_stage(name="Select Year and Instrument",stage=StageSetup)
    pipeline.add_stage(name="Preview and Categorize Images",stage=StagePreview)
    pipeline.add_stage(name="Analyze Pixels",stage=StageAnalyze)
        
    return pipeline

if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()