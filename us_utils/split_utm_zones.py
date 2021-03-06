# -*- coding: utf-8 -*-
"""
Created on Sun Apr 19 11:58:01 2020

@author: disbr007
"""
import argparse
import os
import platform

import geopandas as gpd

from misc_utils.logging_utils import create_logger

logger = create_logger(__name__, 'sh', 'INFO')


def convert_UTM_epsg(utm_zone):
    """
    Converts a utm zone to it's WGS84 EPSG number

    Parameters
    ----------
    utm_zone : STR
        UTM zone in format #,hemisphere (eg. 10,n).

    Returns
    -------
    INT : EPSG.

    """
    number, hemisphere = utm_zone.split(',')
    if hemisphere.lower() == 'n':
        hemi_indicator = 6
    elif hemisphere.lower() == 's':
        hemi_indicator = 7
    else:
        logger.error('Unable to parse utm zone: {}'.format(utm_zone))
    
    epsg = int('32{}{}'.format(hemi_indicator, number))
    
    return epsg


# def create_utm_zone(geometry):
#     centroid = geometry.centroid
#     zone_number = int(math.ceil((centroid.X + 180)/6))
#     if centroid.Y <= 0:
#         utm_epsg = "327%s" % str(zone_number).zfill(2)
#     else:
#         utm_epsg = "326%s" % str(zone_number).zfill(2)

#     return utm_epsg
def locate_utm_zone(feature, utm_zones):
    """
    Locates the UTM zone of a single feature, 
    using the features centroid.

    Parameters
    ----------
    feature : shapely.geometry.object
        A single feature, point, polygon or line. The centroid
        is used.
    utm_zones : geopandas.GeoDataFrame
        The UTM zone polygons. Must have 'Zone_Hemi' field.

    Returns
    -------
    STR : UTM Zone hemisphere and number.

    """
    for i, zone in utm_zones.iterrows():
        if zone.geometry.contains(feature.centroid):
            matched_zone = zone['Zone_Hemi']
            break
        else:
            matched_zone = 'No_match'
    
    return matched_zone


def split_epsg(fp_p, utm_p, out_dir, out_name, dryrun):
    logger.info('Loading footprints: {}'.format(os.path.basename(fp_p)))
    logger.info('Loading UTM zones: {}'.format(os.path.basename(utm_p)))
    fp = gpd.read_file(fp_p)
    utm = gpd.read_file(utm_p)

    logger.info('Determining EPSG zones...')
    # fp_utm = gpd.sjoin(fp, utm, how='left')

    fp['utm'] = fp.geometry.apply(lambda x: locate_utm_zone(x, utm))
    fp['epsg'] = fp.apply(lambda x: convert_UTM_epsg(x['utm']), axis=1)

    # Split and write
    for epsg in list(fp['epsg'].unique()):
        epsg_fp = fp[fp['epsg'] == epsg]
        logger.info('Zone {}: {}'.format(epsg, len(epsg_fp)))
        if not dryrun:
            epsg_out = os.path.join(out_dir, "{}_{}.shp".format(out_name, epsg))
            logger.debug('Writing to file: {}'.format(epsg_out))
            epsg_fp.to_file(epsg_out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Split an input footprint into subfootprints by UTM zone.")

    parser.add_argument('-i', '--input_footprint', type=os.path.abspath,
                        help='Footprint to split.')
    parser.add_argument('-uz', '--utm_zones', type=os.path.abspath,
                        help='Path to utm zones.')
    parser.add_argument('-o', '--out_directory', type=os.path.abspath,
                        help="""Path to directory to write split footprints to.
                                If not provded, input directory will be used.""")
    parser.add_argument('-n', '--out_name', type=os.path.abspath,
                        help="""Basename to use for output files. If not provided,
                                input basename will be used.""")
    parser.add_argument('-d', '--dryrun', action='store_true',
                        help='Print UTM zones without writing split files out.')

    args = parser.parse_args()

    input_footprint = args.input_footprint
    utm_zones = args.utm_zones
    out_directory = args.out_directory
    out_name = args.out_name
    dryrun = args.dryrun

    if not utm_zones:
        system = platform.system()
        if system == 'Windows':
            utm_zones = r'E:\disbr007\general\UTM_Zone_Boundaries\UTM_Zone_Boundaries.shp'
        elif system == 'Linux':
            utm_zones = r'/mnt/pgc/data/scratch/jeff/general/UTM_Zone_Boundaries/UTM_Zone_Boundaries.shp'

    if not out_directory:
        out_directory = os.path.dirname(input_footprint)
    if not out_name:
        out_name = os.path.basename(os.path.splitext(input_footprint)[0])

    split_epsg(input_footprint, utm_zones, out_directory, out_name, dryrun)
