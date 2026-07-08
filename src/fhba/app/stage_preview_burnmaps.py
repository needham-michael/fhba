import os

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
        png_dates = pd.to_datetime([f"{self.year}-" + x.split("YTD")[-1].split(".png")[0] for x in png_files],format='%Y-%j').strftime("%Y-%m-%d")
        png_files = {d:f for d,f in zip(png_dates,png_files)}

        discrete_player  = pn.widgets.DiscretePlayer(
            name='Date',options=list(png_dates),
            visible_buttons=['previous','pause','play','next'],width=250
        )

        print("="*79)
        print(f"{png_dir = }")
        print(f"{png_files = }")
        print(f"{png_dates = }")
        print("="*79)

        return png_files, discrete_player
    
    @param.depends('year', 'registry',)
    def view(self):

        instr = get_instructions("09_instr_preview_burnmask.md", instr_width=250)
        
        png_files, discrete_player = self.setup()

        img_pane = pn.pane.Image(None,width=600)

        def update_image(img_pane, event):

            img_pane.object = png_files[event.new]

        discrete_player.link(img_pane,callbacks={'value':update_image})

        pane = pn.Row(
            instr,
            pn.Column(
                discrete_player,
                img_pane,
                margin=(40, 10), sizing_mode='stretch_both',
                styles={'background': '#f0f0f0'}
            )
        )

        return pane
    
    def panel(self):
        return pn.Row(self.view,)




    