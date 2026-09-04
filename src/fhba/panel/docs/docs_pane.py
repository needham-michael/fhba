import panel as pn
import param

class DocsPane(param.Parameterized):

    def __init__(self):
        self._layout = pn.Column(
            pn.pane.Markdown(
"""
# About

# User Guide
For the full app documentation, see the online __[User Guide](https://needham-michael.github.io/fhba/)__
* __[Case Selection](https://needham-michael.github.io/fhba/User%20Guide/case_select/)__
* __Analysis Pipeline__
    * __[Download Granules](https://needham-michael.github.io/fhba/User%20Guide/Analysis/download_granules/)__
    * __[Process Granules](https://needham-michael.github.io/fhba/User%20Guide/Analysis/process_granules/)__
    * __[Classification](https://needham-michael.github.io/fhba/User%20Guide/Analysis/classify/)__
    * __[Aggregation](https://needham-michael.github.io/fhba/User%20Guide/Analysis/aggregate/)__

""",hard_line_break=True)
        )

    def __panel__(self):
        return self.panel()

    def panel(self):
        return self._layout