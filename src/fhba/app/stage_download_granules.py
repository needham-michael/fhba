
import os
import sys
import panel as pn
import param
import time

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

        table = pn.widgets.Tabulator(df, height=600, width=500,show_index=False)

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
        terminal = pn.widgets.Terminal(height=300, width=800)

        def download_granules(event):

            # Redirect stdout to the terminal widget to show download messages
            sys.stdout = terminal
            
            df_dl = df[df['user_categorization'].isin(checkbox_group.value)]

            if len(df_dl) == 0:
                pn.state.notifications.warning("No granules to download for selected categories.")
                return

            for date in progress_bar(df_dl['date'], desc="Downloading Granules"):
                terminal.write("="*79)
                str_out.object = f"Downloading granules for date: {date}"

                self.gm.prepare_data(date=date)

                terminal.write(f"\nGranules for date {date} downloaded and processed.\n")

            # Return stdout to normal and show completion message
            sys.stdout = sys.__stdout__
            pn.state.notifications.success("Granule download complete.")

        download_button.on_click(download_granules)

        return pn.Column(
                pn.pane.Markdown("### Download Granules for Selected User Categorizations"),
                checkbox_group,
                download_button,
                progress_bar,
                str_out,
                pn.pane.Markdown("### Download Log"),
                terminal,
                margin=(40, 10), width=800,styles={'background': '#f0f0f0'}
            )

    
    @param.depends('year','satellite','registry', 'gm')
    def view(self):

        instr = get_instructions("stage1.md",instr_width=250)
        table_pane = self.table_pane()

        download_pane = self.download_pane()

        pane = pn.Row(
            instr,
            pn.Column(
                table_pane,
                margin=(40, 10), width=500,styles={'background': '#f0f0f0'}
            ),
            pn.Column(
                download_pane,
                margin=(40, 10), width=800,styles={'background': '#f0f0f0'}
            )
        )

        return pane
    
    def panel(self):
        return pn.Row(self.view,)

    