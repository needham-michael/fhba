import os
from datetime import datetime
import importlib.resources
import param
import panel as pn

from fhba.registry import Registry
pn.extension('tabulator')
pn.extension(notifications=True)
    
def build_app():

    registry = Registry().load_json()

    class Stage1(param.Parameterized):

        def get_instructions(self):

            instructions = importlib.resources.open_text(
                'fhba.app.appdata.instructions', 'stage1.md'
                ).readlines()

            return pn.pane.Markdown("".join(instructions))

        year = param.Selector(default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1)))
        satellite = param.Selector(default='SNPP VIIRS', objects=['SNPP VIIRS', 'NOAA-20 VIIRS', 'NOAA-21 VIIRS', 'AQUA MODIS', 'TERRA MODIS'])

        @param.output(('year', param.Integer), ('satellite', param.String))
        def output(self):

            return self.year, self.satellite
        
        @param.depends('year', 'satellite')
        def view(self):
            year, satellite = self.output()
            year_out = pn.pane.Markdown(f"### Analysis Year: {year}")
            sat_out = pn.pane.Markdown(f"### Selected Instrument: {satellite}")
            instructions = self.get_instructions()
            return pn.Column(instructions,year_out, sat_out, margin=(40, 10), width=600,styles={'background': '#f0f0f0'})

        def panel(self):
            return pn.Row(self.param, self.view,)

    class Stage2(param.Parameterized):

        year = param.Selector(default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1)))
        satellite = param.Selector(default='SNPP VIIRS', objects=['SNPP VIIRS', 'NOAA-20 VIIRS', 'NOAA-21 VIIRS', 'AQUA MODIS', 'TERRA MODIS'])

        def get_instructions(self):

            instructions = importlib.resources.open_text(
                'fhba.app.appdata.instructions', 'stage2.md'
                ).readlines()

            return pn.pane.Markdown("".join(instructions))
        
        @param.depends('year', 'satellite',)
        def view(self):

            if str(self.year) not in registry.granule_registry:
                registry.add_granule_registry(str(self.year))
            granule_registry = registry[str(self.year)]


            if self.satellite.split()[0] not in granule_registry.satellites:
                granule_registry.add_satellite(self.satellite.split()[0])
                registry.save_json()

            granule_manager = granule_registry[self.satellite.split()[0]]

            year_out = pn.pane.Markdown(f"### Analysis Year: {self.year}")
            sat_out = pn.pane.Markdown(f"### Selected Instrument: {self.satellite}")
            gran_out = pn.pane.Markdown(f"### Granule Manager: {granule_manager.__str__()}")

            col1 = pn.Column(
                pn.pane.Markdown("## Selected Analysis Parameters"),
                year_out, 
                sat_out, 
                gran_out, self.get_instructions(),
                margin=(40, 10), styles={'background': '#f0f0f0'},
                width=600,
            )

            img_path = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"
            img_pane = pn.pane.Image(img_path,width=400,height=800,visible=True)

            gm_df = granule_manager.to_df().reset_index()
            gm_df = gm_df.rename(columns={'index':'date'})
            gm_df = gm_df[gm_df['download_status'] == True]

            user_categorization_categories = [
                "Fully Cloudy", "Mostly Cloudy", "Mostly Clear", "Fully Clear", "Uncategorized"
            ]

            user_categorization = gm_df['user_categorization']

            user_categorization_selector = {}
            for date, cat in zip(gm_df['date'], gm_df['user_categorization']):
                user_categorization_selector[date] = pn.widgets.Select(
                    name=f"User Categorization for Date: {str(date)}", 
                    options=user_categorization_categories, 
                    value=cat,
                    width=400,
                )

            preview_image_player = pn.widgets.DiscretePlayer(
                name="Date", 
                options=gm_df['date'].tolist(), 
                width=400,
                visible_buttons=['first', 'previous', 'next', 'last'],
                visible_loop_options=[],
            )

            def update_image(event):
                date = preview_image_player.value
                img_path = granule_manager.truecolor_images_by_date.get(date, None)
                if img_path is not None and os.path.exists(img_path):
                    img_pane.object = img_path
                else:
                    img_pane.object = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"

            preview_image_player.param.watch(update_image,"value")
            empty = pn.Spacer(height=0)
            current_selector = pn.bind(lambda date: user_categorization_selector.get(date,empty), preview_image_player)

            save_categorization_button = pn.widgets.Button(name="Save User Categorizations", button_type="primary", width=400)
            def save_categorization(event):
                for date, selector in user_categorization_selector.items():
                    category = selector.value
                    granule_manager.update_user_categorization(date, category)

                registry.save_json()
                pn.state.notifications.success("Categorizations Saved Successfully!")

            save_categorization_button.on_click(save_categorization)

            col2 = pn.Column(
                pn.pane.Markdown("## True Color Image Previews"),
                preview_image_player,
                current_selector,
                save_categorization_button,
                img_pane,
            )

            return pn.Row(col1,col2, margin=(40, 10), styles={'background': '#f0f0f0'})
        
        def panel(self):
            return pn.Row(self.param, self.view,)

    class Stage3(param.Parameterized):

        year = param.Selector(default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1)))
        satellite = param.Selector(default='SNPP VIIRS', objects=['SNPP VIIRS', 'NOAA-20 VIIRS', 'NOAA-21 VIIRS', 'AQUA MODIS', 'TERRA MODIS'])

        def get_instructions(self):

            instructions = importlib.resources.open_text(
                'fhba.app.appdata.instructions', 'stage3.md'
                ).readlines()

            return pn.pane.Markdown("".join(instructions))
        
        @param.output(('date', param.String))
        def output(self):

            return self.year, self.satellite
        
        @param.depends('year', 'satellite')
        def view(self):

            granule_registry = registry[str(self.year)]
            granule_manager = granule_registry[self.satellite.split()[0]]
            user_categories = granule_manager.user_categorization_by_date

            gm_df = granule_manager.to_df().reset_index()
            gm_df = gm_df.rename(columns={'index':'date'})
            gm_df = gm_df[gm_df['download_status'] == True]

            date_selector = pn.widgets.Select(name="", options=gm_df['date'].tolist(), width=400)

            table = pn.widgets.Tabulator(gm_df, show_index=False, width=800)

            instructions = self.get_instructions()

            col1 = pn.Column(
                instructions,
                pn.pane.Markdown("## Select Date for Analysis"),
                date_selector,
                pn.layout.Divider(),
                pn.pane.Markdown("## Data Preview"),
                table,
                width=800,
                margin=(40, 10),styles={'background': '#f0f0f0'}
            )

            return col1

        def panel(self):
            return pn.Row(self.param, self.view,)

    pipeline = pn.pipeline.Pipeline()
    pipeline.add_stage('Select Year and Instrument', Stage1)
    pipeline.add_stage('Preview and Categorize Dates', Stage2)
    pipeline.add_stage('Select Date for Analysis', Stage3)

    app = pn.Column(
        pn.pane.Markdown("# FHBA: Flint Hills Burn Area Tool"),
        pn.layout.Divider(),
        pipeline
    )

    return app
    
if __name__.startswith("bokeh"):
    app = build_app()
    app.servable()

