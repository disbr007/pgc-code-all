# -*- coding: utf-8 -*-
"""
Created on Tue Aug 27 15:36:40 2019

@author: disbr007
Clip raster to shapefile extent. Must be in the same projection.
"""

import shutil

from osgeo import ogr, gdal
import os, logging, argparse

# import geopandas as gpd
# import shapely

from misc_utils.gdal_tools import check_sr, ogr_reproject, get_raster_sr, remove_shp
from misc_utils.id_parse_utils import read_ids
from misc_utils.logging_utils import create_logger


gdal.UseExceptions()
ogr.UseExceptions()


logger = create_logger(__name__, 'sh', 'DEBUG')


def warp_rasters(shp_p, rasters, out_dir=None, out_suffix='_clip',
                 out_prj_shp=None, in_mem=False, overwrite=False):
    """
    Take a list of rasters and warps (clips) them to the shapefile feature
    bounding box.
    rasters : LIST or STR
        List of rasters to clip, or if STR, path to single raster.
    out_prj_shp : os.path.abspath
        Path to create the projected shapefile if necessary to match raster prj.
        ** CURRENTLY MUST PROVIDE THIS ARG **
    """
    # TODO: Fix permission error if out_prj_shp not supplied -- create in-mem OGR?
    # Use in memory directory if specified
    if out_dir is None:
        in_mem = True
    if in_mem:
        out_dir = r'/vsimem'

    # Check that spatial references match, if not reproject (assumes all rasters have same projection)
    # TODO: support different extension (slow to check all of them in the loop below)
    # Check if list of rasters provided or if single raster
    if isinstance(rasters, list):
        check_raster = rasters[0]
    else:
        check_raster = rasters
        rasters = [rasters]

    logger.debug('Checking spatial reference match:\n{}\n{}'.format(shp_p, check_raster))
    sr_match = check_sr(shp_p, check_raster)
    if not sr_match:
        logger.debug('Spatial references do not match.')
        if not out_prj_shp:
            out_prj_shp = shp_p.replace('.shp', '_prj.shp')
        shp_p = ogr_reproject(shp_p,
                              to_sr=get_raster_sr(check_raster),
                              output_shp=out_prj_shp)

    # Do the 'warping' / clipping
    warped = []
    for raster_p in rasters:
        raster_p = raster_p.replace(r'\\', os.sep)
        raster_p = raster_p.replace(r'/', os.sep)

        if not out_dir:
            out_dir == os.path.dirname(raster_p)
        # print('od: {}'.format(out_dir))

        # Clip to shape
        logger.debug('Clipping {}...'.format(os.path.basename(raster_p)))
        # Create outpath
        raster_out_name = '{}{}.tif'.format(os.path.basename(raster_p).split('.')[0], out_suffix)
        # print('ron: {}'.format(raster_out_name))
        raster_op = os.path.join(out_dir, raster_out_name)
        # print('rop: {}'.format(raster_op))
        if os.path.exists(raster_op) and not overwrite:
            pass
        else:
            raster_ds = gdal.Open(raster_p)
            x_res = raster_ds.GetGeoTransform()[1]
            y_res = raster_ds.GetGeoTransform()[5]
            warp_options = gdal.WarpOptions(cutlineDSName=shp_p, cropToCutline=True,
                                            targetAlignedPixels=True, xRes=x_res, yRes=y_res)
            gdal.Warp(raster_op, raster_ds, options=warp_options)
            # Close the raster
            raster_ds = None
            logger.debug('Clipped raster created at {}'.format(raster_op))
            # Add clipped raster path to list of clipped rasters to return
            warped.append(raster_op)

    # Remove projected shp
    if in_mem is True:
        remove_shp(out_prj_shp)

    return warped


def move_meta_files(raster_p, out_dir, raster_ext=None):
    """Move metadata files associted with raster, skipping files with
       raster_ext if specified"""
    src_dir = os.path.dirname(raster_p)
    raster_name = os.path.splitext(os.path.basename(raster_p))[0]
    other_files = os.listdir(src_dir)
    meta_files = [f for f in other_files if f.startswith(raster_name)]
    if raster_ext:
        meta_files = [f for f in meta_files if not f.endswith(raster_ext)]

    for src_f in meta_files:
        src = os.path.join(src_dir, src_f)
        shutil.copy(src, out_dir)


def clip_rasters(shp_p, rasters, out_dir=None, out_suffix='_clip',
                 out_prj_shp=None, raster_ext=None, move_meta=False, 
                 in_mem=False, overwrite=False):
    """
    Take a list of rasters and warps (clips) them to the shapefile feature
    bounding box.
    rasters : LIST or STR
        List of rasters to clip, or if STR, path to single raster.
    out_prj_shp : os.path.abspath
        Path to create the projected shapefile if necessary to match raster prj.
    """
    # TODO: Fix permission error if out_prj_shp not supplied -- create in-mem OGR?
    # Use in memory directory if specified
    if out_dir is None:
        in_mem = True
    if in_mem:
        out_dir = r'/vsimem'

    # Check that spatial references match, if not reproject (assumes all rasters have same projection)
    # TODO: support different extension (slow to check all of them in the loop below)
    # Check if list of rasters provided or if single raster
    if isinstance(rasters, list):
        check_raster = rasters[0]
    else:
        check_raster = rasters
        rasters = [rasters]

    logger.debug('Checking spatial reference match:\n{}\n{}'.format(shp_p, check_raster))
    sr_match = check_sr(shp_p, check_raster)
    if not sr_match:
        logger.debug('Spatial references do not match. Reprojecting to AOI...')
        if not out_prj_shp:
            out_prj_shp = shp_p.replace('.shp', '_prj.shp')
        shp_p = ogr_reproject(shp_p,
                              to_sr=get_raster_sr(check_raster),
                              output_shp=out_prj_shp)

    # Do the 'warping' / clipping
    warped = []
    for raster_p in rasters:
        raster_p = raster_p.replace(r'\\', os.sep)
        raster_p = raster_p.replace(r'/', os.sep)

        if not out_dir:
            out_dir == os.path.dirname(raster_p)

        # Clip to shape
        logger.debug('Clipping {}...'.format(os.path.basename(raster_p)))
        # Create outpath
        raster_out_name = '{}{}.tif'.format(os.path.basename(raster_p).split('.')[0], out_suffix)
        # print('ron: {}'.format(raster_out_name))
        raster_op = os.path.join(out_dir, raster_out_name)
        # print('rop: {}'.format(raster_op))
        if os.path.exists(raster_op) and not overwrite:
            pass
        else:
            raster_ds = gdal.Open(raster_p, gdal.GA_ReadOnly)
            x_res = raster_ds.GetGeoTransform()[1]
            y_res = raster_ds.GetGeoTransform()[5]
            warp_options = gdal.WarpOptions(cutlineDSName=shp_p, cropToCutline=True,
                                            targetAlignedPixels=True, xRes=x_res, yRes=y_res)
            gdal.Warp(raster_op, raster_ds, options=warp_options)
            # Close the raster
            raster_ds = None
            logger.debug('Clipped raster created at {}'.format(raster_op))
            # Add clipped raster path to list of clipped rasters to return
            warped.append(raster_op)
        # Move meta-data files if specified
        if move_meta:
            logger.debug('Moving metadata files to clip destination...')
            move_meta_files(raster_p, out_dir, raster_ext=raster_ext)

    # Remove projected shp
    if in_mem is True:
        remove_shp(out_prj_shp)

    # If only one raster provided, just return the single path as str
    if len(warped) == 1:
        warped = warped[0]

    return warped


if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument('shape_path', type=os.path.abspath, help='Shape to clip rasters to.')
    parser.add_argument('rasters', nargs='*', type=os.path.abspath,
                        help='Rasters to clip. Either paths directly to, directory, or text file of paths.')
    parser.add_argument('out_dir', type=os.path.abspath, help='Directory to write clipped rasters to.')
    parser.add_argument('--out_suffix', type=str, help='Suffix to add to clipped rasters.')
    parser.add_argument('--raster_ext', type=str, default='.tif', help='Ext of input rasters.')
    parser.add_argument('--move_meta', action='store_true',
                        help='Use this flag to move associated meta-data files to clip destination.')
    parser.add_argument('--dryrun', action='store_true', help='Prints inputs without running.')

    args = parser.parse_args()

    shp_path = args.shape_path
    rasters = args.rasters
    out_dir = args.out_dir
    out_suffix = args.out_suffix
    move_meta = args.move_meta

    # Check if list of rasters given or directory
    if os.path.isdir(args.rasters[0]):
        r_ps = os.listdir(args.rasters[0])
        rasters = [os.path.join(args.rasters[0], r_p) for r_p in r_ps if r_p.endswith(args.raster_ext)]
    elif args.rasters[0].endswith('.txt'):
        rasters = read_ids(args.rasters[0])

    if args.dryrun:
        print('Input shapefile:\n{}'.format(shp_path))
        print('Input rasters:\n{}'.format('\n'.join(rasters)))
        print('Output directory:\n{}'.format(out_dir))

    else:
        clip_rasters(shp_path, rasters, out_dir, out_suffix, raster_ext=args.raster_ext,
                     move_meta=move_meta)
