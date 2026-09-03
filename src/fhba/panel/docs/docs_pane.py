import panel as pn
import param

class DocsPane(param.Parameterized):

    def __init__(self):
        self._layout = pn.Column(
            pn.pane.Markdown(
"""
# About

# User Guide
For the full app documentation, see the online __[User Guide]()__
* __[Case Selection]()__
* __Analysis Pipeline__
    * __[Download Granules]()__
    * __[Process Granules]()__
    * __[Classification]()__
    * __[Aggregation]()__

""",hard_line_break=True)
        )

    def __panel__(self):
        return self.panel()

    def panel(self):
        return self._layout