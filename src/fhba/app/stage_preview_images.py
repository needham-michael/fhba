import os
import panel as pn
import param

from fhba.app.utils import get_instructions


class StagePreviewImages(param.Parameterized):

    year = param.Integer()
    satellite = param.String()
    registry = param.Parameter()
    gm = param.Parameter()

    @param.depends('registry', 'gm')
    def img_pane(self):

        img_path = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"
        img_pane = pn.pane.Image(img_path, width=400, height=800, visible=True)

        gm_df = self.gm.to_df().reset_index()
        gm_df = gm_df.rename(columns={'index': 'date'})
        gm_df = gm_df[~gm_df['truecolor_images_by_date'].isna()]

        preview_image_player = pn.widgets.DiscretePlayer(
            name="Date",
            options=gm_df['date'].tolist(),
            width=400,
            visible_buttons=['first', 'previous', 'next', 'last'],
            visible_loop_options=[],
        )

        user_categorization_categories = [
            "Fully Cloudy", "Mostly Cloudy", "Mostly Clear", "Fully Clear", "Uncategorized"
        ]

        user_categorization_selector = {}
        for date, cat in zip(gm_df['date'], gm_df['user_categorization']):
            user_categorization_selector[date] = pn.widgets.Select(
                name=f"User Categorization for Date: {str(date)}",
                options=user_categorization_categories,
                value=cat,
                width=400,
            )

        def update_image(event):
            date = preview_image_player.value
            img_path = self.gm.truecolor_images_by_date.get(date, None)
            if img_path is not None and os.path.exists(img_path):
                img_pane.object = img_path
            else:
                img_pane.object = "https://www.kgs.ku.edu/Publications/OFR/2012/OFR12_6/Flint_Hills_Ecoregion.jpg"

        preview_image_player.param.watch(update_image, "value")
        empty = pn.Spacer(height=0)
        current_categorization_selector = pn.bind(
            lambda date: user_categorization_selector.get(date, empty),
            preview_image_player
        )

        save_categorization_button = pn.widgets.Button(
            name="Save User Categorizations", button_type="primary", width=400
        )

        # ── Auto-categorize from cloud mask ───────────────────────────────────
        auto_categorize_button = pn.widgets.Button(
            name="Auto-categorize by Cloud Mask", button_type="default", width=400
        )

        auto_status = pn.pane.Markdown("", width=400)

        def save_categorization(event):
            for date, selector in user_categorization_selector.items():
                category = selector.value
                self.gm.update_user_categorization(date, category)
            self.registry.save_json()
            pn.state.notifications.success("Categorizations Saved Successfully!")

        save_categorization_button.on_click(save_categorization)

        def auto_categorize(event):
            """Auto-populate user categorizations from processed cloud mask files."""
            dates_with_masks = [
                d for d in gm_df['date'].tolist()
                if d in self.gm.processed_cloud_masks_by_date
                and self.gm.processed_cloud_masks_by_date[d]
            ]

            if not dates_with_masks:
                pn.state.notifications.warning(
                    "No processed cloud masks found. "
                    "Download and process granules before auto-categorizing.",
                    duration=6000)
                return

            n_updated = 0
            for date in dates_with_masks:
                category = self.gm.auto_categorize_cloud_cover(date)
                if date in user_categorization_selector:
                    user_categorization_selector[date].value = category
                n_updated += 1

            self.registry.save_json()
            auto_status.object = (
                f"✅ Auto-categorized **{n_updated}** date(s) from cloud mask data."
            )
            pn.state.notifications.success(
                f"Auto-categorized {n_updated} date(s) from cloud masks.", duration=5000)

        auto_categorize_button.on_click(auto_categorize)

        return pn.Column(
            preview_image_player,
            current_categorization_selector,
            pn.Row(save_categorization_button),
            pn.Row(auto_categorize_button),
            auto_status,
            img_pane,
            sizing_mode='stretch_both'
        )

    @param.depends('year', 'satellite', 'registry')
    def view(self):
        instr = get_instructions("03_instr_categorize_previews.md", instr_width=250)
        img_pane = self.img_pane()

        pane = pn.Row(
            pn.Column(
                instr,
                pn.pane.Alert(
                    "These categorizations determine which dates are downloaded in the next step. "
                    "Use 'Auto-categorize by Cloud Mask' if granules have already been downloaded.",
                    sizing_mode='stretch_width'),
                width=250, margin=(40, 10),
            ),
            pn.Column(
                img_pane,
                margin=(10, 10), sizing_mode='stretch_both', styles={'background': '#f0f0f0'}
            )
        )

        return pane

    def panel(self):
        return pn.Row(self.view,)
