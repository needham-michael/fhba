import os
from collections import defaultdict

import panel as pn
import param

class StageViewBurnmasks(param.Parameterized):
    year = param.Integer()
    registry = param.Parameter()

    # Declare parameters (empty objects; will be filled in __init__)
    sat_combo = param.Selector(label='Satellite Combination',objects=[], default=None)
    start_date = param.Selector(objects=[], default=None)

    def __init__(self,**params):
        super().__init__(**params)
        self._setup()

        self._layout = pn.Row(
            pn.WidgetBox(
            self.param.sat_combo, 
            self.param.start_date,
            ),
            pn.Column(
                self._date_slider,
                self._img_pane,
                self._img_pane_path
            )
        )

    def _setup(self):
        
        # Initialize descrete slider for end date
        self._date_slider = pn.widgets.DiscretePlayer(
            name = "Date",
            options=[],
            visible_buttons=["previous","pause","play","next"],
            show_loop_controls=False
        )

        self._img_pane = pn.pane.Image(None,width=800,height=600,visible=True)
        self._img_pane_path = pn.pane.Markdown()

        self._date_slider.param.watch(self._update_image,"value")
        
        self._get_dates_by_sat()
        
        # Initialize sat_combo options
        sats = list(self._start_dates_by_sat.keys())
        self.param['sat_combo'].objects = sats
        self.sat_combo = sats[0]   # pick a default

        # Initialize start_date options based on the default sat_combo
        start_dates = self._start_dates_by_sat[self.sat_combo]
        self.param.start_date.objects = start_dates
        self.start_date = start_dates[0]

    def _get_dates_by_sat(self):
        self._start_dates_by_sat = {}
        self._end_dates_by_sat = defaultdict(dict)
        _sat_combos = self.registry.processed_burnmasks[str(self.year)].keys()
        
        for _sat_combo in _sat_combos:
            self._start_dates_by_sat[_sat_combo] = [
                k.split("-")[0] for k in self.registry.processed_burnmasks[str(self.year)][_sat_combo].keys()
            ]

        for _sat_combo in self._start_dates_by_sat:
            for _start_date in self._start_dates_by_sat[_sat_combo]:
                self._end_dates_by_sat[_sat_combo][_start_date] = []
                for _date_pair in self.registry.processed_burnmasks[str(self.year)][_sat_combo].keys():
                    _d1, _d2 = _date_pair.split("-")
                    if _d1 == _start_date:
                        self._end_dates_by_sat[_sat_combo][_start_date].append(_d2)

    @param.depends('sat_combo', watch=True)
    def _update_start_dates(self):
        _start_dates = self._start_dates_by_sat[self.sat_combo]
        self.param.start_date.objects = _start_dates
        self.start_date = _start_dates[0]

    @param.depends('start_date', watch=True)
    def _update_date_slider(self):
        _end_dates = self._end_dates_by_sat[self.sat_combo][self.start_date]
        self._date_slider.options = _end_dates
        self._date_slider.value = _end_dates[0]

    def _update_image(self,event):
        end_date = self._date_slider.value
        key = f"{self.start_date}-{end_date}"
        img_path = self.registry.processed_burnmasks_png[str(self.year)][self.sat_combo][key]
        if img_path is not None and os.path.exists(img_path):
            self._img_pane.object = img_path
            self._img_pane_path.object =  f"Image stored at: `{img_path}`"
        else:
            self._img_pane.object = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"
            self._img_pane_path.object = ""

    def __panel__(self):
        return self.panel()

    def panel(self):
        return self._layout