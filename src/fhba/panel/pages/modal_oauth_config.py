import panel as pn
import param

pn.extension()

class ModalOAuthConfig(param.Parameterized):
    def __init__(self,close_button):
        self._input_client_id = pn.widgets.TextInput(label="Client ID")
        self._input_client_secret = pn.widgets.PasswordInput(label="Client Secret")
        self._loading = pn.indicators.LoadingSpinner(size=35,value=False,name="")
        self._button_enter = pn.widgets.Button(
            label="Store Credentials",on_click=self._register_credentials,color='primary')
        self._button_close = close_button
        self._layout = pn.WidgetBox(
            "# Update SentinelHub Credentials",
            self._input_client_id,
            self._input_client_secret,
            pn.Row(self._button_enter,self._loading),
            self._button_close
        )

    def _register_credentials(self,event):
        from sentinelhub import SHConfig, SentinelHubSession

        config = SHConfig(
            sh_client_id = self._input_client_id.value,
            sh_client_secret = self._input_client_secret.value,
            sh_base_url = "https://sh.dataspace.copernicus.eu",
            sh_token_url = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
        )

        try: 
            self._loading.value = True
            self._loading.name = "Checking Credentials..."
            session = SentinelHubSession(config=config)
            token = session.token
            config.save()
            pn.state.notifications.success("SentinelHub Credentials Validated and Saved.")
        except:
            pn.state.notifications.error("SentinelHub Credentials Not Valid.")

        self._loading.value = False
        self._loading.name = ""


    def __panel__(self):
        return self.panel()

    def panel(self):
        return self._layout

def get_oauth_instructions():
    instr = "# OAuth Instructions\n"
    instr += "Placeholder for OAuth Authentication Instructions"

    return pn.pane.Markdown(instr,hard_line_break=True)