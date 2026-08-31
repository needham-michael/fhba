import panel as pn
import param

from fhba.panel.utils import style

class StageSelectBlendMethod(param.Parameterized):
 
    year = param.Integer()
    satellite_full = param.String()
    satellite = param.String()
    registry = param.Parameter()
    valid_max_date = param.String() # dates stored as strings like "YYYY-MM-DD"
    valid_min_date = param.String()
    sat_info = param.Parameter()
    sat_band_subset = param.List()
    granules = param.Parameter()
    blend_method = param.Selector(objects=['Stack','Weighted By Sensor Zenith Angle','By Granule Name'])

    def __init__(self,**params):
        super().__init__(**params)
        self._get_style()
        self._setup()

        self._layout = pn.Card(pn.Column(
            pn.pane.Alert("## Mosaicking Not Yet Implemented - Simple Pass-Thru Stage",alert_type='info'),
            self.param.blend_method
        ),**self.card)

    def _setup(self):
        self.sat_band_subset += list(sorted(self.sat_info.band_list_minimal))
        self.sat_band_subset = list(set(self.sat_band_subset)) # ensure no duplicate bands
        self.granules = self.registry.granules[str(self.year)][self.satellite_full]
        
    def _get_style(self):
        style_dict = style()
        for key in style_dict:
            setattr(self,key,style_dict[key])

    def panel(self):
        return self._layout