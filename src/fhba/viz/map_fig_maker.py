from dataclasses import dataclass
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
import pandas as pd
import rioxarray as rxr

from blume.table import table

from fhba.viz import shp2gdf

@dataclass
class MapFigMaker:
    date_range : str
    sat_combo : str
    fname_tif : str | Path
    crs : ccrs.CRS
    fig_title : str
    county_shp : Path

    def __post_init__(self):
        self.start_date, self.end_Date = pd.to_datetime(self.date_range.split("-")).strftime("%b %d, %Y")

        self.fname_csv = Path(str(self.fname_tif).replace(".tif",".csv"))
        self.fname_png = Path(str(self.fname_tif).replace(".tif",".png"))

        self.read_files()
        self.format_burntable_cell_text()
        

    def format_burntable_cell_text(self,):
    
        # Format the table for display
        self.df['County'] = [c.split()[0] + "  " for c in self.df['county_name']]
        self.df['State'] = [s + "  " for s in self.df['state_name']]
        self.df['Acres Burned'] = [f"{x:,.0f} " for x in self.df['burned_area_acres']]
        
        cellCols = ['County','State','Acres Burned']
        cellText = [list(x) for x in list(self.df[cellCols].to_numpy())]
        cellText.append(['TOTAL',"",f"{self.df['burned_area_acres'].sum():,.0f}"])
    
        self.cellText = cellText
        self.cellCols = cellCols

    def read_files(self):
        self.df = pd.read_csv(self.fname_csv)
        self.county_gdf = shp2gdf(self.county_shp).to_crs(self.crs)
        self.ds = rxr.open_rasterio(self.fname_tif).squeeze().rio.clip(self.county_gdf['geometry'])

    def make_figure(self):
        fig, [ax,ax_table] = plt.subplots(
            ncols=2,
            subplot_kw=dict(projection=self.crs,frameon=False),
            dpi=600,
            layout='compressed'
        )

        self.county_gdf.plot(ax=ax,facecolor='beige',edgecolor='k',linewidth=0.25)
        ax.pcolormesh(self.ds.x,self.ds.y,self.ds.where(self.ds==1),transform=self.crs,zorder=10,cmap='Reds_r')

        table(ax_table,cellText=self.cellText,colLabels=self.cellCols,cellLoc='right',bbox=(0,0,1,1))
        ax_table.annotate(
            f"Based on Satellite Imagery from {self.sat_combo}",
            xy=(0,-0.05),xycoords='axes fraction',fontsize='xx-small',ha='left',fontfamily='monospace')

        fig.suptitle(self.fig_title,ha='center',fontsize='medium')

        # Add labels to counties
        for geom, label in zip(self.county_gdf['geometry'],self.county_gdf['NAME']):
            x,y = geom.centroid.xy
            ax.text(s=label.split()[0],x=x[0],y=y[0],ha='center',fontsize=4,fontweight='bold')
    
        plt.savefig(self.fname_png,bbox_inches='tight')