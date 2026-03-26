
import os
import sys
import panel as pn
import param
from time import perf_counter

import earthaccess
from earthaccess.exceptions import LoginAttemptFailure

from fhba.app.utils import get_instructions

pn.extension("tabulator","terminal")

class StageDownloadGranules(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year','satellite','registry', 'gm')
    def table_pane(self):

        df = self.gm.to_df().reset_index()
        df = df.rename(columns={'index':'date'})
        df = df[['date','user_categorization','download_status']]

        table = pn.widgets.Tabulator(df, height=600, show_index=False)

        by_cat = df.groupby('user_categorization').count().iloc[:,0]

        table_col = pn.Column(
            pn.pane.Markdown("### Granule Download Status by User Categorization"),
            pn.pane.Str(by_cat.to_string()),
            table
        )

        return table_col
    
    @param.depends('year','satellite','registry', 'gm')
    def download_pane(self):

        df = self.gm.to_df().reset_index()
        df = df.rename(columns={'index':'date'})

        download_button = pn.widgets.Button(name="Download Granules for Selected Categories", button_type="primary")
        checkbox_group = pn.widgets.CheckBoxGroup(
            name="Select User Categorizations to Download Granules For",
            options=["Fully Clear", "Mostly Clear", "Mostly Cloudy", "Fully Cloudy", "Uncategorized"],
            inline=True,
            value=["Fully Clear"]
        )

        str_out = pn.pane.Str(None, width=400)
        progress_bar = pn.widgets.Tqdm()
        terminal = pn.widgets.Terminal(height=300, width=800,options={"cursorBlink": True})


        password_box = pn.widgets.PasswordInput(name='password')
        username_box = pn.widgets.TextInput(name='username')
        auth_earthaccess_button = pn.widgets.Button(name="Authenticate NASA Earthdata",button_type='primary')
        auth_loading = pn.indicators.LoadingSpinner(
            name='Authenticating Earthaccess...',visible=False,size=20)

        def auth_earthaccess(event):

            # Redirect stdout to the terminal widget to show download messages
            sys.stdout = terminal

            password = password_box.value
            username = username_box.value

            auth_loading.value = True
            auth_loading.visible = True

            os.environ['EARTHDATA_USERNAME'] = username
            os.environ['EARTHDATA_PASSWORD'] = password

            try:
                earthaccess.login(strategy='environment')
                pn.state.notifications.success("Success!")
            except LoginAttemptFailure:
                pn.state.notifications.error("ERROR AUTHENTICATING")

            auth_loading.value = False
            auth_loading.visible = False

            # Return stdout to normal and show completion message
            sys.stdout = sys.__stdout__

        auth_earthaccess_button.on_click(auth_earthaccess)

        def download_granules(event):

            # Redirect stdout to the terminal widget to show download messages
            sys.stdout = terminal

            df_dl = df[df['user_categorization'].isin(checkbox_group.value)]

            if len(df_dl) == 0:
                pn.state.notifications.warning("No granules to download for selected categories.")
                return

            for date in progress_bar(df_dl['date'], desc="Downloading Granules"):
                start_time = perf_counter()
                terminal.write("="*79 + "\n")
                str_out.object = f"Downloading granules for date: {date}"

                self.gm.prepare_data(date=date)

                end_time = perf_counter()
                duration = end_time - start_time
                terminal.write(f"\nGranules for date {date} downloaded and processed in {duration:.6f} seconds\n")

                self.registry.save_json()

            # Return stdout to normal and show completion message
            sys.stdout = sys.__stdout__
            pn.state.notifications.success("Granule download complete.")

        download_button.on_click(download_granules)

        return pn.Column(
                pn.pane.Markdown("### Download Granules for Selected User Categorizations"),
                checkbox_group,
                username_box,
                password_box,
                pn.Row(auth_earthaccess_button,auth_loading),
                download_button,
                progress_bar,
                str_out,
                pn.pane.Markdown("### Download Log"),
                terminal,
                margin=(10, 10), sizing_mode='stretch_both',styles={'background': '#f0f0f0'}
            )

    
    @param.depends('year','satellite','registry', 'gm')
    def view(self):

        instr = get_instructions("04_instr_download_granules.md",instr_width=250)
        table_pane = self.table_pane()

        download_pane = self.download_pane()

        pane = pn.Row(
            pn.Column(
                instr,
                pn.pane.Alert("Please be patient, downloading and processing satellite granules can take several minutes complete for each date",sizing_mode='stretch_width',alert_type='warning'),
                width=250, margin=(40, 10),
            ),
            pn.Column(
                table_pane,
                margin=(10, 10),sizing_mode='stretch_both',styles={'background': '#f0f0f0'}
            ),
            pn.Column(
                download_pane,
                margin=(10, 10),width=820,styles={'background': '#f0f0f0'}
            )
        )

        return pane
    
    def panel(self):
        return pn.Row(self.view,)

    