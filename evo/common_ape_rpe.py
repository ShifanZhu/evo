"""
Common functions for evo_ape and evo_rpe, internal only.
author: Michael Grupp

This file is part of evo (github.com/MichaelGrupp/evo).

evo is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

evo is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with evo.  If not, see <http://www.gnu.org/licenses/>.
"""

import argparse
import logging
import typing
from pathlib import Path

from evo.core.filters import FilterException
from evo.core.metrics import PoseRelation, Unit
from evo.core.result import Result
from evo.core.trajectory import PosePath3D, PoseTrajectory3D

logger = logging.getLogger(__name__)

SEP = "-" * 80  # separator line


def load_trajectories(
    args: argparse.Namespace
) -> typing.Tuple[PosePath3D, PosePath3D, str, str]:
    from evo.tools import file_interface

    traj_ref: typing.Union[PosePath3D, PoseTrajectory3D]
    traj_est: typing.Union[PosePath3D, PoseTrajectory3D]

    if args.subcommand == "tum":
        traj_ref = file_interface.read_tum_trajectory_file(args.ref_file)
        traj_est = file_interface.read_tum_trajectory_file(args.est_file)
        ref_name, est_name = args.ref_file, args.est_file
    elif args.subcommand == "kitti":
        traj_ref = file_interface.read_kitti_poses_file(args.ref_file)
        traj_est = file_interface.read_kitti_poses_file(args.est_file)
        ref_name, est_name = args.ref_file, args.est_file
    elif args.subcommand == "euroc":
        traj_ref = file_interface.read_euroc_csv_trajectory(args.state_gt_csv)
        traj_est = file_interface.read_tum_trajectory_file(args.est_file)
        ref_name, est_name = args.state_gt_csv, args.est_file
    elif args.subcommand in ("bag", "bag2"):
        logger.debug("Opening bag file " + args.bag)
        if not Path(args.bag).exists():
            raise file_interface.FileInterfaceException(
                "File doesn't exist: {}".format(args.bag))
        if args.subcommand == "bag2":
            from rosbags.rosbag2 import Reader as Rosbag2Reader
            bag = Rosbag2Reader(args.bag)  # type: ignore
        else:
            from rosbags.rosbag1 import Reader as Rosbag1Reader
            bag = Rosbag1Reader(args.bag)  # type: ignore
        try:
            bag.open()
            traj_ref = file_interface.read_bag_trajectory(
                bag, args.ref_topic, cache_tf_tree=True)
            traj_est = file_interface.read_bag_trajectory(
                bag, args.est_topic, cache_tf_tree=True)
            ref_name, est_name = args.ref_topic, args.est_topic
        finally:
            bag.close()
    else:
        raise KeyError("unknown sub-command: {}".format(args.subcommand))

    return traj_ref, traj_est, ref_name, est_name


def get_pose_relation(args: argparse.Namespace) -> PoseRelation:
    if args.pose_relation == "full":
        pose_relation = PoseRelation.full_transformation
    elif args.pose_relation == "rot_part":
        pose_relation = PoseRelation.rotation_part
    elif args.pose_relation == "trans_part":
        pose_relation = PoseRelation.translation_part
    elif args.pose_relation == "angle_deg":
        pose_relation = PoseRelation.rotation_angle_deg
    elif args.pose_relation == "angle_rad":
        pose_relation = PoseRelation.rotation_angle_rad
    elif args.pose_relation == "point_distance":
        pose_relation = PoseRelation.point_distance
    elif args.pose_relation == "point_distance_error_ratio":
        pose_relation = PoseRelation.point_distance_error_ratio
    return pose_relation


def get_delta_unit(args: argparse.Namespace) -> Unit:
    delta_unit = Unit.none
    if args.delta_unit == "f":
        delta_unit = Unit.frames
    elif args.delta_unit == "d":
        delta_unit = Unit.degrees
    elif args.delta_unit == "r":
        delta_unit = Unit.radians
    elif args.delta_unit == "m":
        delta_unit = Unit.meters
    elif args.delta_unit == "s":
        delta_unit = Unit.seconds
    return delta_unit


def downsample_or_filter(args: argparse.Namespace, traj_ref: PosePath3D,
                         traj_est: PosePath3D) -> None:
    logger.debug(SEP)
    old_num_poses_ref = traj_ref.num_poses
    old_num_poses_est = traj_est.num_poses
    if args.downsample:
        logger.debug("Downsampling trajectories to max %d poses.",
                     args.downsample)
        traj_ref.downsample(args.downsample)
        traj_est.downsample(args.downsample)
    if args.motion_filter:
        if not all(
                isinstance(t, PoseTrajectory3D) for t in (traj_ref, traj_est)):
            raise FilterException("trajectories without timestamps can't be "
                                  "motion filtered in metrics because it "
                                  "could break the required synchronization")
        distance_threshold = args.motion_filter[0]
        angle_threshold = args.motion_filter[1]
        logger.debug(
            "Filtering trajectories with motion filter "
            "thresholds: %f m, %f deg", distance_threshold, angle_threshold)
        traj_ref.motion_filter(distance_threshold, angle_threshold, True)
        traj_est.motion_filter(distance_threshold, angle_threshold, True)
    logger.debug("Number of poses in reference was reduced from %d to %d.",
                 old_num_poses_ref, traj_ref.num_poses)
    logger.debug("Number of poses in estimate was reduced from %d to %d.",
                 old_num_poses_est, traj_est.num_poses)


def plot_result(args: argparse.Namespace, result: Result, traj_ref: PosePath3D,
                traj_est: PosePath3D,
                traj_ref_full: typing.Optional[PosePath3D] = None) -> None:
    from evo.tools import plot
    from evo.tools.settings import SETTINGS

    import matplotlib.pyplot as plt
    import numpy as np

    logger.debug(SEP)
    logger.debug("Plotting results... ")
    plot_mode = plot.PlotMode(args.plot_mode)

    # Plot the raw metric values.
    fig1 = plt.figure(figsize=SETTINGS.plot_figsize)
    if (args.plot_x_dimension == "distances"
            and "distances_from_start" in result.np_arrays):
        x_array = result.np_arrays["distances_from_start"]
        x_label = "$d$ (m)"
    elif (args.plot_x_dimension == "seconds"
          and "seconds_from_start" in result.np_arrays):
        x_array = result.np_arrays["seconds_from_start"]
        x_label = "$t$ (s)"
    else:
        x_array = None
        x_label = "index"

    plot.error_array(
        fig1.gca(), result.np_arrays["error_array"], x_array=x_array,
        statistics={
            s: result.stats[s]
            for s in SETTINGS.plot_statistics if s not in ("min", "max")
        }, name=result.info["label"], title=result.info["title"],
        xlabel=x_label)

    # Plot the values color-mapped onto the trajectory.
    fig2 = plt.figure(figsize=SETTINGS.plot_figsize)
    ax = plot.prepare_axis(
        fig2, plot_mode,
        length_unit=Unit(SETTINGS.plot_trajectory_length_unit))

    plot.traj(ax, plot_mode, traj_ref_full if traj_ref_full else traj_ref,
              style=SETTINGS.plot_reference_linestyle,
              color=SETTINGS.plot_reference_color, label='reference',
              alpha=SETTINGS.plot_reference_alpha,
              plot_start_end_markers=SETTINGS.plot_start_end_markers)
    plot.draw_coordinate_axes(ax, traj_ref, plot_mode,
                              SETTINGS.plot_reference_axis_marker_scale)

    if args.plot_colormap_min is None:
        args.plot_colormap_min = result.stats["min"]
    if args.plot_colormap_max is None:
        args.plot_colormap_max = result.stats["max"]
    if args.plot_colormap_max_percentile is not None:
        args.plot_colormap_max = np.percentile(
            result.np_arrays["error_array"], args.plot_colormap_max_percentile)

    plot.traj_colormap(ax, traj_est, result.np_arrays["error_array"],
                       plot_mode, min_map=args.plot_colormap_min,
                       max_map=args.plot_colormap_max,
                       title=result.info["title"],
                       plot_start_end_markers=SETTINGS.plot_start_end_markers)
    plot.draw_coordinate_axes(ax, traj_est, plot_mode,
                              SETTINGS.plot_axis_marker_scale)
    if args.ros_map_yaml:
        plot.ros_map(ax, args.ros_map_yaml, plot_mode)
    if SETTINGS.plot_pose_correspondences:
        plot.draw_correspondence_edges(
            ax, traj_est, traj_ref, plot_mode,
            style=SETTINGS.plot_pose_correspondences_linestyle,
            color=SETTINGS.plot_reference_color,
            alpha=SETTINGS.plot_reference_alpha)
    fig2.axes.append(ax)

    plot_collection = plot.PlotCollection(result.info["title"])
    plot_collection.add_figure("raw", fig1)
    plot_collection.add_figure("map", fig2)
    if args.plot:
        plot_collection.show()
    if args.save_plot:
        plot_collection.export(args.save_plot,
                               confirm_overwrite=not args.no_warnings)
    if args.serialize_plot:
        logger.debug(SEP)
        plot_collection.serialize(args.serialize_plot,
                                  confirm_overwrite=not args.no_warnings)
    plot_collection.close()
