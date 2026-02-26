
import os
import pandas as pd
import panel as pn
import param

pn.extension()

class StageDownloadPreviews(param.Parameterized):
    """Stage to control downloding preview satellite images"""

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year','satellite','registry', 'gm')
    def download_pane(self):

        download_image_button = pn.widgets.Button(name="Download Preview Images", button_type="primary")
        date_range_slider = pn.widgets.DateRangeSlider(
            start=pd.to_datetime(self.gm.start_date), 
            end=pd.to_datetime(self.gm.end_date), 
            name="Date Range",
            format='%Y-%m-%d'
        )

        progress_bar = pn.widgets.Tqdm()

        png_pane = pn.pane.PNG(None,width=400,height=400,fixed_aspect=True)
        png_label = pn.pane.Markdown("Preview Image for Date:",width=400)

        os.makedirs(self.gm.truecolor_img_dir, exist_ok=True)

        def download_images(event):

            date_range = pd.date_range(date_range_slider.value[0], date_range_slider.value[1],freq='1D')

            for date in progress_bar(date_range, desc="Downloading Preview Images"):

                date = date.strftime("%Y-%m-%d")

                png_name = f"{self.gm.satellite_name.replace('-','')}_{self.gm.instrument}_{self.gm.spatial_name}_truecolor_{date}.png"
                truecolor_file = os.path.join(self.gm.truecolor_img_dir, png_name)

                self.gm.retrieve_worldview_image(
                    date=date, out_path=truecolor_file,overwrite=False
                    )
                
                png_pane.object = truecolor_file
                png_label.object = f"Preview Image for Date: {date}"

                self.registry.save_json()

        download_image_button.on_click(download_images)

        return pn.Column(date_range_slider,download_image_button,progress_bar,png_label,png_pane)
    
    @param.depends('year','satellite','registry', 'gm')
    def view(self):

        return pn.Column(
            pn.pane.Markdown("## Download Preview Images"),
            self.download_pane,
            margin=(40, 10), styles={'background': '#f0f0f0'}
        )
    
    def panel(self):
        return pn.Row(self.view,)