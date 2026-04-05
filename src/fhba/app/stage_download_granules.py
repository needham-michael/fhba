import logging
import os
import sys
import panel as pn
import param
from time import perf_counter

import earthaccess
from earthaccess.exceptions import LoginAttemptFailure

from fhba.app.utils import get_instructions

logger = logging.getLogger(__name__)

pn.extension("tabulator", "terminal")


class StageDownloadGranules(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('year', 'satellite', 'registry', 'gm')
    def table_pane(self):

        df = self.gm.to_df().reset_index()
        df = df.rename(columns={'index': 'date'})
        df = df[['date', 'user_categorization', 'download_status']]

        table = pn.widgets.Tabulator(df, height=600, show_index=False)

        by_cat = df.groupby('user_categorization').count().iloc[:, 0]

        table_col = pn.Column(
            pn.pane.Markdown("### Granule Download Status by User Categorization"),
            pn.pane.Str(by_cat.to_string()),
            table
        )

        return table_col

    @param.depends('year', 'satellite', 'registry', 'gm')
    def download_pane(self):

        df = self.gm.to_df().reset_index()
        df = df.rename(columns={'index': 'date'})

        download_button = pn.widgets.Button(
            name="Download Granules for Selected Categories",
            button_type="primary"
        )
        checkbox_group = pn.widgets.CheckBoxGroup(
            name="Select User Categorizations to Download Granules For",
            options=["Fully Clear", "Mostly Clear", "Mostly Cloudy", "Fully Cloudy", "Uncategorized"],
            inline=True,
            value=["Fully Clear"]
        )
        resampler_selector = pn.widgets.Select(
            name="Resampler",
            options=['nearest', 'ewa'],
            value='nearest',
            width=120,
        )
        resampler_help = pn.pane.Markdown(
            "**nearest** – fast (seconds per date, recommended)  \n"
            "**ewa** – accurate elliptical weighted averaging (10–20 min per date)",
            width=400,
        )

        str_out = pn.pane.Str(None, width=400)
        progress_bar = pn.widgets.Tqdm()
        terminal = pn.widgets.Terminal(
            height=300, width=800, options={"cursorBlink": True})

        # ── Authentication ─────────────────────────────────────────────────────
        # Check whether earthaccess was already authenticated in Stage 1.
        # If credentials are already valid, hide the login form.
        try:
            _already_auth = earthaccess.get_auth().authenticated
        except Exception:
            _already_auth = False

        auth_status_md = pn.pane.Markdown(
            "✅ **NASA Earthdata: authenticated** (credentials saved from Stage 1).",
            visible=_already_auth,
            width=400
        )

        password_box = pn.widgets.PasswordInput(
            name='NASA Earthdata Password', visible=not _already_auth)
        username_box = pn.widgets.TextInput(
            name='NASA Earthdata Username', visible=not _already_auth)
        auth_earthaccess_button = pn.widgets.Button(
            name="Authenticate NASA Earthdata",
            button_type='primary',
            visible=not _already_auth
        )
        auth_loading = pn.indicators.LoadingSpinner(
            name='Authenticating Earthaccess...', visible=False, size=20)

        def auth_earthaccess(event):
            sys.stdout = terminal

            password = password_box.value
            username = username_box.value

            auth_loading.value = True
            auth_loading.visible = True

            os.environ['EARTHDATA_USERNAME'] = username
            os.environ['EARTHDATA_PASSWORD'] = password

            try:
                earthaccess.login(strategy='environment')
                # Persist credentials to ~/.netrc so the user doesn't have to
                # re-enter them on the next app restart.
                try:
                    earthaccess.login(persist=True)
                except Exception:
                    pass  # non-fatal — credentials still valid for this session
                pn.state.notifications.success(
                    "Earthdata authentication successful! Credentials saved for future sessions.")
                # Hide manual login widgets — no longer needed
                username_box.visible = False
                password_box.visible = False
                auth_earthaccess_button.visible = False
                auth_status_md.visible = True
            except LoginAttemptFailure:
                pn.state.notifications.error("Authentication failed. Check username/password.")

            auth_loading.value = False
            auth_loading.visible = False
            sys.stdout = sys.__stdout__

        auth_earthaccess_button.on_click(auth_earthaccess)

        def download_granules(event):
            sys.stdout = terminal

            df_dl = df[df['user_categorization'].isin(checkbox_group.value)]

            # For Landsat, restrict to known orbital pass dates only so we
            # don't waste API calls on days where no swath crosses the area.
            known_pass_dates = self.gm.get_known_pass_dates()
            if known_pass_dates is not None:
                before = len(df_dl)
                df_dl = df_dl[df_dl['date'].isin(known_pass_dates)]
                skipped = before - len(df_dl)
                if skipped:
                    terminal.write(
                        f"Landsat pass filter: skipping {skipped} non-pass dates "
                        f"({len(df_dl)} known pass dates remain).\n")

            if len(df_dl) == 0:
                pn.state.notifications.warning(
                    "No granules to download for selected categories.")
                sys.stdout = sys.__stdout__
                return

            for date in progress_bar(df_dl['date'], desc="Downloading Granules"):
                start_time = perf_counter()
                terminal.write("=" * 79 + "\n")
                str_out.object = f"Downloading granules for date: {date}"

                self.gm.prepare_data(date=date, resampler=resampler_selector.value)

                end_time = perf_counter()
                duration = end_time - start_time
                terminal.write(
                    f"\nGranules for date {date} downloaded and processed "
                    f"in {duration:.1f} seconds\n")

                self.registry.save_json()

            sys.stdout = sys.__stdout__
            pn.state.notifications.success("Granule download complete.")

        download_button.on_click(download_granules)

        return pn.Column(
            pn.pane.Markdown("### Download Granules for Selected User Categorizations"),
            checkbox_group,
            pn.layout.Divider(),
            pn.pane.Markdown("**Resampling Method**"),
            pn.Row(resampler_selector),
            resampler_help,
            pn.layout.Divider(),
            pn.pane.Markdown("**NASA Earthdata Authentication**"),
            auth_status_md,
            username_box,
            password_box,
            pn.Row(auth_earthaccess_button, auth_loading),
            pn.layout.Divider(),
            download_button,
            progress_bar,
            str_out,
            pn.pane.Markdown("### Download Log"),
            terminal,
            margin=(10, 10), sizing_mode='stretch_both', styles={'background': '#f0f0f0'}
        )

    @param.depends('year', 'satellite', 'registry', 'gm')
    def view(self):

        instr = get_instructions("04_instr_download_granules.md", instr_width=250)
        table_pane = self.table_pane()
        download_pane = self.download_pane()

        pane = pn.Row(
            pn.Column(
                instr,
                pn.pane.Alert(
                    "Please be patient — downloading and processing satellite granules "
                    "can take several minutes per date.",
                    sizing_mode='stretch_width', alert_type='warning'),
                width=250, margin=(40, 10),
            ),
            pn.Column(
                table_pane,
                margin=(10, 10), sizing_mode='stretch_both', styles={'background': '#f0f0f0'}
            ),
            pn.Column(
                download_pane,
                margin=(10, 10), width=820, styles={'background': '#f0f0f0'}
            )
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)
