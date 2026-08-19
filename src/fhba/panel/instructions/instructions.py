import panel as pn
import param

class Instructions(param.Parameterized):

    def __init__(self):
        self._layout = pn.pane.Markdown(f"## Placeholder for App Instructions")
        
    def panel(self):
        return self._layout