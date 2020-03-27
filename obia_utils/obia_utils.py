# -*- coding: utf-8 -*-
"""
Created on Sat Mar 14 12:27:22 2020

@author: disbr007
"""

# import os
# import logging.config
from random import randint
from tqdm import tqdm

import numpy as np
from osgeo import ogr, gdal
import pandas as pd
import geopandas as gpd

from misc_utils.logging_utils import create_logger #LOGGING_CONFIG
from misc_utils.RasterWrapper import Raster


gdal.UseExceptions()

# logging.config.dictConfig(LOGGING_CONFIG('INFO'))
logger = create_logger(__name__, 'sh', 'INFO')


def get_value(df, lookup_field, lookup_value, value_field):
    val = df[df[lookup_field]==lookup_value][value_field]
    if len(val) == 0:
        logger.error('Lookup value not found: {} in {}'.format(lookup_value, lookup_field))
    elif len(val) > 1:
        logger.error('Lookup value occurs more than once: {} in {}'.format(lookup_value, lookup_field))
    
    return val.values[0]


def get_neighbors(gdf, subset=None, unique_id=None, neighbor_field='neighbors'):
    """
    Gets the neighbors for a geodataframe with polygon geometry
    geodataframe, optionally only a subset.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        GeoDataFrame to compute neighbors in, must be polygon/multipolygon.
    subset : gpd.GeoDataFrame
        Selected rows from gdf to compute neighbors for. Highly recommended 
        for large dataframes as neighbor computation can be slow.
    unique_id : str
        Unique field name in gdf and subset to use as identifier. The default is None.
    neighbor_field : str
        The name of the field to create to store neighbor unique_ids.

    Returns
    -------
    result : gpd.GeoDataFrame
        GeoDataFrame with added column containing list of unique IDs of neighbors.

    """
    
    # If no subset is provided, use the whole dataframe
    if subset is None:
        subset = gdf
    
    # List to store neighbors
    ns = []
    # List to store unique_ids
    labels = []
    # Iterate over rows, for each row, get unique_ids of all features it touches
    logger.info('Getting neighbors for {} features...'.format(len(subset)))
    for index, row in tqdm(subset.iterrows(), total=len(subset)):
        neighbors = gdf[gdf.geometry.touches(row['geometry'])][unique_id].tolist()
        # If the feature is considering itself a neighbor remove it from the list
        # TODO: clean this logic up (or just the comment) 
        #       when does a feature find itself as a neighbor?
        if row[unique_id] in neighbors:
            neighbors = neighbors.remove(row[unique_id])
        
        # Save the neighbors that have been found and their IDs
        ns.append(neighbors)
        labels.append(row[unique_id])

    # Create data frame of the unique ids and their neighbors
    nebs = pd.DataFrame({unique_id:labels, neighbor_field:ns})
    # Combine the neighbors dataframe back into the main dataframe, joining on unique_id
    # essentially just adding the neighbors column
    result = pd.merge(gdf,
                      nebs,
                      how='left',
                      on=unique_id)
    logger.info('Neighbor computation complete.')
    
    return result


def neighbor_features(unique_id, gdf, subset=None, neighbor_ids_col=None):
    """
    Create a geodataframe of neighbors for all features in subset. Finds neighbors if
    neighbor_ids_col does not exist already.

    Parameters
    ----------
    unique_id : str
        Column containing unique ids for each feature.
    gdf : gpd.GeoDataFrame
        Full geodataframe containing all features.
    subset : gpd.GeoData, optional
        Subset of gdf containing only feautres to find neighbors for. The default is None.
    neighbor_ids_col : str, optional
        Column in subset (and gdf) containing neigbor unique IDs. If column doesn't exist,
        the column name in which to put neighbor IDs, The default is None.

    Returns
    -------
    neighbor_feats : gpd.GeoDataFrame
        GeoDataFrame containing one row per nieghbor for each row in subset. Will contain
        repeated geometries if features in subset share neighbors.

    """
    # Compute for entire dataframe if subset is not provided.
    if not isinstance(subset, (gpd.GeoDataFrame, pd.DataFrame)):
        subset = gdf
    
    # Find neighbors if column containing neighbor IDs does not already exist
    if not neighbor_ids_col in subset.columns:
        subset = get_neighbors(gdf=gdf, subset=subset, unique_id=unique_id, 
                               neighbor_field=neighbor_ids_col)
    
    # Store source IDs and neighbor IDs in lists
    source_ids = []
    neighbor_ids = []
    for i, row in subset.iterrows():
        # Get all neighbors of current feature, as list, add to master list
        neighbors = row[neighbor_ids_col]
        neighbor_ids.extend(neighbors)
        # Add source ID to list one time for each of its neighbors
        for n in neighbors:
            source_ids.append(row[unique_id])
    # Create 'look up' dataframe of source IDs and neighbor ids            
    src_lut = pd.DataFrame({'neighbor_src': source_ids, 'neighbor_id': neighbor_ids})
    
    # Find each neighbor feature in the master GeoDataFrame, creating a new GeoDataFrame
    neighbor_feats = gpd.GeoDataFrame()
    for ni in neighbor_ids:
        feat = gdf[gdf[unique_id]==ni]
        neighbor_feats = pd.concat([neighbor_feats, feat])
    
    # Join neighbor features to sources
    # This is one-to-many with one row for each neighbor-source pair
    neighbor_feats = pd.merge(neighbor_feats, src_lut, left_on=unique_id, right_on='neighbor_id')
    # Remove redundant neighbor_id column - this is the same as the unique_id in this df
    neighbor_feats.drop(columns=['neighbor_id'], inplace=True)
    
    return neighbor_feats


def neighbor_values(df, unique_id, neighbors, value_field):
    """
    Look up the values of a list of neighbors. Returns dict of id:value
    
    Parameters
    ----------
    df : pd.DataFrame
        Dataframe to look up values in
    unique_id : str
        Name of field in df with unique values and field where neighbor values are found
    neighbors : list
        List of unique_id's to get values of
    value_field : str
        Name of field to return values from.
    
    Returns
    -------
    dict : unique_id of neighbor : value_field value
    
    """
    # For each neighbor id in the list of neighbor ids, create an entry in 
    # a dictionary that is the id and its value.
    values = {n:df[df[unique_id]==n][value_field].values[0] for n in neighbors}
    
    return values
    

def neighbor_adjacent(gdf, subset, unique_id,
                      adjacent_field='adj_thresh',
                      neighbor_field='neighbors', 
                      value_field=None, value_thresh=None, value_compare=None):
    
    """
    For each feature in subset, determines if it is adjacent to feature meeting "value" requirements.
    For example, is the feature adjacent to another feature with a mean_slope > 10.
    
    Parameters
    ----------
    gdf : gpd.GeoDataFrame (or pd.DataFrame)
        GeoDataFrame to find adjacent features in.
    subset : gpd.GeoDataFrame
        Selected rows from gdf to compute adjacency for. Highly recommended 
        for large dataframes as neighbor computation can be slow.
    unique_id : str
        Unique field name in gdf and subset to use as identifier.
    adjacent_field : str
        Name of field to create to hold Boolean output of whether feature meets adjacent reqs.
    neighbor_field : str
        Name of field to create to hold neighbors, temporary.
    value_field : str
        Name of field to use in evaluating adjacent threshold.
    value_thresh : int/float/str
        Value of value field to use in threshold.
    value_compare : str
        The operator to use to compare feature value to value thresh. One of ['<', '>', '==', '!=']
    
    Returns
    -------
    result : gpd.GeoDataFrame (or pd.Dataframe)
        DataFrame with added field [adjacent_field], which is a boolean series indicating
        whether the feature meets requirements of having a neighbor that meets the value
        threshold indicated.

    """
    # Find the IDs of all the features in subset, store in neighbor field
    if neighbor_field not in gdf.columns:
        gdf = get_neighbors(gdf, subset=subset, unique_id=unique_id,
                            neighbor_field=neighbor_field)
    # Use all of the IDs in subset to pull out unique_ids and their neighbor lists
    subset_ids = subset[unique_id]
    neighbors = gdf[gdf[unique_id].isin(subset_ids)][[unique_id, neighbor_field]]
    
    # Iterate over features and check if any meet threshold
    logger.info('Finding adjacent features that meet threshold...')
    have_adj = []
    for index, row in tqdm(neighbors.iterrows(), total=len(neighbors)):
        if row['label'] == 163516:
            print('******************match********************')
        values = neighbor_values(gdf, unique_id, row[neighbor_field], value_field)
        if value_compare == '<':
            ## Assuming value compare operator is less than for testing
            matches = [v < value_thresh for k, v in values.items()]
        elif value_compare == '>':
            matches = [v > value_thresh for k, v in values.items()]
        elif value_compare == '==':
            matches = [v == value_thresh for k, v in values.items()]
        elif value_compare == '!=':
            matches = [v != value_thresh for k, v in values.items()]
        else:
            logger.error("""value_compare operater not recognized, must be
                            one of: ['<', '>', '==', '!='. 
                            value_compare: {}""".format(value_compare))
        if any(matches):
            have_adj.append(row[unique_id])
    
    # If feature had neighbor meeting threshold, return True, else False
    gdf[adjacent_field] = gdf[unique_id].isin(have_adj)
    
    return gdf


def mask_class(gdf, column, raster, out_path, mask_value=1):
    """
    Mask (set to NoData) areas of raster where column == mask_value in gdf.
    Designed to mask an already classified area from subsequent 
    segmentations/classifications, or to mask everything except class-candidate 
    areas for resegmentation.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        GeoDataFrame containing column and polygon geometries to be masked.
    column : str
        Name of column containing values to burn into raster.
    raster : str
        Path to raster to be masked.
    out_path : str
        Path to masked raster. This can be an in-memory location ('/vsimem/temp.tif').
    mask_value : int, float, str, optional
        The value of column, where corresponding geometries should be set to NoData
        in output raster. The default is 1.

    Returns
    -------
    out_path : str

    """
    # Random number to append to temporary filenames to *attempt* to avoid overwriting if
    # multiprocessing
    ri = randint(0, 1000)

    # Save class to temporary vector file
    temp_seg = r'/vsimem/temp_seg_class{}.shp'.format(ri)
    gdf[[column, 'geometry']].to_file(temp_seg)
    vect_ds = ogr.Open(temp_seg)
    vect_lyr = vect_ds.GetLayer()
    
    # Get metadata from raster to be burned into
    img = Raster(raster)
    ulx, uly, lrx, lry = img.get_projwin()
    
    # Create output datasource with same metadata as input raster
    temp_rast = r'/vsimem/temp_seg_rast{}.vrt'.format(ri)
    target_ds = gdal.GetDriverByName('GTiff').Create(temp_rast, img.x_sz, img.y_sz, 1, img.dtype)
    target_ds.SetGeoTransform(img.geotransform)
    target_band = target_ds.GetRasterBand(1)
    target_band.SetNoDataValue(img.nodata_val)
    target_band.FlushCache()
    
    # Rasterize attribute into output datasource
    gdal.RasterizeLayer(target_ds, [1], vect_lyr, options=["ATTRIBUTE={}".format(column)])
    
    # Read rasterized layer as array
    t_arr = target_ds.ReadAsArray()
    target_ds = None
    
    # Get original image as array
    o_arr = img.MaskedArray
    
    # Convert where the column is value to no data in the orginal image, keeping other original
    # values
    new = np.where(t_arr==mask_value, img.nodata_val, o_arr)
    
    # Write the updated array/image out
    img.WriteArray(new, out_path)
        
    return out_path