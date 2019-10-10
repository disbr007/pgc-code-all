# -*- coding: utf-8 -*-
"""
Created on Fri Feb 15 13:45:51 2019

@author: disbr007
"""
import os
import imageio
import argparse


def make_gif(prj_path, out_name, ext, duration=1.5):
    """
    Create a gif from a give path, considering only
    those files that end with ext. Scenes are displayed
    for duration.
    """
    print('Images to combine: ')
    image_paths = []
    for file_name in os.listdir(prj_path):
        if file_name.endswith(ext):
            file_path = os.path.join(prj_path, file_name)
            image_paths.append(file_path)
    image_paths.sort()
    images = [imageio.imread(f) for f in image_paths]
    # images.append(imageio.imread(file_path))
    print('Ordered file paths: ')
    print(image_paths)
    imageio.mimsave(os.path.join(prj_path, '{}.gif'.format(out_name)), images, duration=duration)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("project_path", type=str, help='The directory containing images to combine')
    parser.add_argument("out_name", type=str, help='The name of the gif to be created')
    parser.add_argument("image_type", type=str, help="The format of the images to combine. E.g: 'png', 'jpg'.")
    parser.add_argument("--duration", "-d", dest='duration', type=float, required=False, help="Duration to show each scene, in seconds (default=1.5)")
    args = parser.parse_args()
    print('Making gif...')
    make_gif(args.project_path, args.out_name, args.image_type, args.duration)
    print('Complete. gif at: {}'.format(os.path.join(args.project_path, '{}.gif'.format(args.out_name))))
