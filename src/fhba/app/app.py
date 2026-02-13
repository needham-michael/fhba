import importlib
import os
from datetime import datetime

import holoviews as hv
import geoviews as gv
import geopandas as gpd
import numpy as np
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
    def table_pane(self):

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index':'date'})
        gm_df = gm_df[gm_df['download_status'] == True]

        table = pn.widgets.Tabulator(
            gm_df[['date','user_categorization','analysis_status']], show_index=False, width=400
        )

        return table, gm_df


    @param.depends('year','satellite','registry', 'gm')
    def view(self):
        instr = get_instructions("stage3.md")

        table, gm_df = self.table_pane()

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

        hv_rgb = self.gm.get_nir_red_hv_rgb(date=analysis_date_selector.value)
        hv_pane = pn.pane.HoloViews(hv_rgb * county_overly, width=500, height=1000)

        loading = pn.indicators.LoadingSpinner(name="Loading Image...", width=200, height=50,visible=False,value=False)


        def update_hv_rgb(event):
            date = analysis_date_selector.value

            print(f"Updating HV RGB with {date = }")  # Debugging statement
            
            loading.value = True
            loading.visible = True
            rgb = self.gm.get_nir_red_hv_rgb(date=date,in_app=True)
            

            if isinstance(rgb, str):
                pn.state.notifications.error(rgb)
                return
            
            if rgb is not None:
                hv_pane.object = rgb * county_overly

            loading.value = False
            loading.visible = False
        
        load_img_button.on_click(
            lambda event: pn.state.notifications.info(f"Loading image for analysis date: {analysis_date_selector.value}")
        )

        load_img_button.on_click(update_hv_rgb)
            
            
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
                pn.Row(hv_pane,loading)),
            margin=(40, 10), styles={'background': '#f0f0f0'})

    def panel(self):
        return pn.Row(self.view,)

def get_instructions(filename,instr_width=instr_width):

    instructions = importlib.resources.open_text(
        'fhba.app.appdata.instructions', filename
    ).readlines()

    return pn.pane.Markdown("".join(instructions),width=instr_width)

def build_app():
    
    pipeline = pn.pipeline.Pipeline(debug=True)
    pipeline.add_stage(name="Select Year and Instrument",stage=StageSetup)
    pipeline.add_stage(name="Preview and Categorize Images",stage=StagePreview)
    pipeline.add_stage(name="Analyze Pixels",stage=StageAnalyze)
        
    return pipeline

if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()