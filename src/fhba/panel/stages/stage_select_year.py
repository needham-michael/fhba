from datetime import datetime

import panel as pn
import param

from fhba.panel.utils import style, get_valid_dates

class StageSelectYear(param.Parameterized):

    year = param.Selector(
        default=datetime.now().year, objects=list(range(2017, datetime.now().year + 1))
        )

    registry = param.Parameter()

    def __init__(self,registry,**params):
        super().__init__(**params)
        self.registry = registry
        self._get_style()

        self._layout = pn.Card(pn.Column(
            pn.pane.Markdown("## Select Year to Aggregate Burnmasks"),
            self.param.year
        ),**self.card)

    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout
