import panel as pn
import param
import fhba

class VersionInfo(param.Parameterized):

    def __init__(self):
        app_version = fhba.__version__

        self._layout = pn.pane.Markdown(f"#### *Flint Hills Burned Area Tool (FHBA App Version {app_version})*")
        
    def panel(self):
        return self._layout