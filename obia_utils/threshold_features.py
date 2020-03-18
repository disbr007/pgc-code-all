# -*- coding: utf-8 -*-
"""
Created on Tue Feb 18 21:50:31 2020

@author: disbr007
"""

# import logging.config
# import os
import matplotlib.pyplot as plt
import numpy as np
# from random import randint
# from tqdm import tqdm

# import pandas as pd
import geopandas as gpd
# from osgeo import gdal, gdalconst, ogr


from misc_utils.logging_utils import create_logger, create_module_loggers
# from obia_utils import neighbor_adjacent
from obia_utils.obia_utils import neighbor_adjacent, mask_class


logger = create_logger(__name__, 'sh', 'INFO')
module_loggers = create_module_loggers('sh', 'INFO')


# Parameters
slope_thresh = 20
tpi41_thresh = -1 # value threshold for tpi41_mean field
# Field names
# Created
merge = 'merge'
steep = 'steep' # features above slope threshold
neighb = 'neighbors' # field to hold neighbor unique ids
headwall = 'headwall' # field - bool - headwall = True
# Existing
unique_id = 'label'
slope_mean = 'slope_mean'
tpi41_mean = 'tpi41_mean'


# Inputs
seg_path = r'V:\pgc\data\scratch\jeff\ms\2020feb01\aoi6\seg\WV02_20150906_pcatdmx_slope_a6g_sr5_rr1_0_ms400_tx500_ty500_stats.shp'
tks_bounds_p = r'E:\disbr007\umn\ms\shapefile\tk_loc\digitized_thaw_slumps.shp'

# Load data
logger.info('Loading segmentation...')
seg = gpd.read_file(seg_path)
tks = gpd.read_file(tks_bounds_p)
logger.info('Loaded {} segments.'.format(len(seg)))

# Load digitzed thermokarst boundaries
tks = gpd.read_file(tks_bounds_p)
tks = tks[tks['obs_year']==2015]
# Select only those thermokarst features within segmentation bounds
xmin, ymin, xmax, ymax = seg.total_bounds
tks = tks.cx[xmin:xmax, ymin:ymax]

# Determine features above steepness threshold
seg[steep] = seg[slope_mean] > slope_thresh

# Classify headwall by adjacent to tpi < param
seg = neighbor_adjacent(seg, subset=seg[seg['steep']==True],
                        unique_id=unique_id,
                        neighbor_field=neighb,
                        adjacent_field=headwall,
                        value_field=tpi41_mean,
                        value_thresh=tpi41_thresh,
                        value_compare='<')

seg['h1'] = np.where(seg[headwall]==True, 1, 0)

#### Remove class from segmentation


# # Raster to be segmented
img_p = r'V:\pgc\data\scratch\jeff\ms\2020feb01\aoi6\dems\slope\WV02_20150906_pcatdmx_slope_a6g.tif'

out = mask_class(seg, 'h1', img_p, r'/vsimem/test_raster.vrt', mask_value=1)





#### Plotting
# Set up
plt.style.use('ggplot')
fig, ax = plt.subplots(1,1, figsize=(10,10))
fig.set_facecolor('darkgray')
ax.set_yticklabels([])
ax.set_xticklabels([])

# Plot full segmentation with no fill
seg.plot(facecolor='none', linewidth=0.5, ax=ax, edgecolor='grey')
# Plot the digitized RTS boundaries
tks.plot(facecolor='none', edgecolor='black', linewidth=2, ax=ax)
# Plot the classified features
# seg[seg[steep]==True].plot(facecolor='b', alpha=0.75, ax=ax)
seg[seg[headwall]==True].plot(facecolor='r', ax=ax, alpha=0.5)


plt.tight_layout()



