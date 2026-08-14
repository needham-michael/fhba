import os

import panel as pn
import param

from fhba.panel.utils import style

class StageSortTruecolor(param.Parameterized):
    """Pipeline stage to view and sort TrueColor images for further analysis"""
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

        if self._previews_located:
            self._layout = pn.Card(pn.Column(
                self._preview_image_player,
                self._current_categorization_selector,
                self._button_save,
                self._img_pane
            ),title=f"Satellite: {self.satellite_full}; Year: {self.year}",**self.card)
        else:
            self._layout = pn.pane.Alert(
                f"## No TrueColor Previews Downloaded for {self.satellite} during {self.year}",alert_type='warning')

    def _setup(self):
        self._previews_located = True
        try:
            self.granules = self.registry.granules[str(self.year)][self.satellite_full]
        except KeyError:
            self._previews_located = False
            return

        self._user_categorization = {d: self.granules[d].categorization for d in self.granules.keys()}
        
        
        self._user_categorization_categories = [
            "Fully Cloudy", "Mostly Cloudy", "Mostly Clear",
            "Fully Clear", "Uncategorized"
        ]

        # Widgets
        self._img_pane = pn.pane.Image(None,width=400,height=800,visible=True)
        self._preview_image_player = pn.widgets.DiscretePlayer(
            name="Date", 
            options=list(self.granules.keys()), 
            width=400,
            visible_buttons=['first', 'previous', 'next', 'last'],
            visible_loop_options=[],
        )
        self._button_save = pn.widgets.Button(name="Save User Categorizations",
            **self.button_primary,on_click=self._save_categorizations
        )

        self._user_categorization_selector = {}
        for date, cat in zip(self._user_categorization.keys(),self._user_categorization.values()):
            self._user_categorization_selector[date] = pn.widgets.Select(
                name=f"User Categorization for Date: {str(date)}", 
                options=self._user_categorization_categories, 
                value=cat,
                width=400,
            )

        empty = pn.Spacer(height=0)
        self._current_categorization_selector = pn.bind(
            lambda date: self._user_categorization_selector.get(date,empty), 
            self._preview_image_player
        )

        self._preview_image_player.param.watch(self._update_image,"value")

    def _update_image(self,event):
        date = self._preview_image_player.value
        img_path = self.granules[date].files.truecolor_img_path
        if img_path is not None and os.path.exists(img_path):
            self._img_pane.object = img_path
        else:
            self._img_pane.object = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"

    def _save_categorizations(self,event):
        for date, selector in self._user_categorization_selector.items():
            category = selector.value

            self.granules[date].categorization = category

        self.registry.granules[str(self.year)][self.satellite_full] = self.granules

        self.registry.to_json()

        pn.state.notifications.success("Categorizations Updated.")

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout
