import os
import re

import pandas as pd
import panel as pn
import param

from glob import glob

from fhba.app.utils import get_instructions

class StagePreviewBurnmaps(param.Parameterized):

    year = param.Integer()
    registry = param.Parameter()

    def setup(self):

        png_dir = os.sep.join(self.registry.raw_data_dir.split(os.sep)[:-1])
        png_dir = os.sep.join([png_dir,"final_burnmask"])
        png_dir = os.sep.join([png_dir,str(self.year)])

        png_files = glob(png_dir + os.sep + "*.png")

        # Group each png file based on its start date
        png_files_by_start_date = {}
        p = re.compile(r"\d{7}-\d{7}.png")
        for png_file in png_files:
            start_date, end_date = p.search(png_file).group().replace(".png","").split("-")
            if start_date in png_files_by_start_date:
                png_files_by_start_date[start_date][end_date] = png_file
            else:
                png_files_by_start_date[start_date] = {end_date:png_file}

        # png_dates = pd.to_datetime([f"{self.year}-" + x.split("YTD")[-1].split(".png")[0] for x in png_files],format='%Y-%j').strftime("%Y-%m-%d")
        # png_files = {d:f for d,f in zip(png_dates,png_files)}

        start_date_selector = pn.widgets.Select(
            options=list(png_files_by_start_date.keys())
            )

        discrete_player  = pn.widgets.DiscretePlayer(
            name='Date',
            # options=list(png_files_by_start_date.values())[0],
            options=list(list(png_files_by_start_date.values())[0].keys()),
            visible_buttons=['previous','pause','play','next'],width=250
        )

        def update_discrete_player_options(event):
            discrete_player.options = list(png_files_by_start_date[event.new].keys())

        start_date_selector.param.watch(update_discrete_player_options, 'value')

        return png_files_by_start_date, start_date_selector, discrete_player
    
    @param.depends('year', 'registry',)
    def view(self):

        instr = get_instructions("09_instr_preview_burnmask.md", instr_width=250)
        
        png_files_by_start_date, start_date_selector, discrete_player = self.setup()

        print(f"{png_files_by_start_date = }")

        img_pane = pn.pane.Image(None,width=600)

        # Update options to discrete player based on selection of start date
        def update_player(event):
            discrete_player.options = list(png_files_by_start_date[event.new].keys())

        start_date_selector.param.watch(update_player, 'value')

        def update_image(event):
            start_date = start_date_selector.value
            end_date = discrete_player.value

            print(f"{start_date = }")
            print(f"{end_date = }")

            img_pane.object = png_files_by_start_date[start_date][end_date]

        discrete_player.param.watch(update_image, 'value')

        # discrete_player.link(img_pane,callbacks={'value':update_image})

        pane = pn.Row(
            instr,
            pn.Column(
                start_date_selector,
                discrete_player,
                img_pane,
                margin=(40, 10), sizing_mode='stretch_both',
                styles={'background': '#f0f0f0'}
            )
        )

        return pane
    
    def panel(self):
        return pn.Row(self.view,)




    