# -*- coding: UTF8 -*-
"""
some functions for trajectories
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

import logging
import typing
from enum import Enum, unique

import numpy as np
import math

from evo import EvoException
import evo.core.transformations as tr
import evo.core.geometry as geometry
from evo.core import lie_algebra as lie
from evo.core import filters
from scipy.spatial.transform import Rotation as R

logger = logging.getLogger(__name__)


class TrajectoryException(EvoException):
    pass


@unique
class Plane(Enum):
    """
    Planes embedded in R3, e.g. for projection purposes.
    """
    XY = "xy"
    XZ = "xz"
    YZ = "yz"


class PosePath3D(object):
    """
    just a path, no temporal information
    also: base class for real trajectory
    """
    def __init__(
            self, positions_xyz: typing.Optional[np.ndarray] = None,
            orientations_quat_wxyz: typing.Optional[np.ndarray] = None,
            poses_se3: typing.Optional[typing.Sequence[np.ndarray]] = None,
            meta: typing.Optional[dict] = None):
        """
        :param positions_xyz: nx3 list of x,y,z positions
        :param orientations_quat_wxyz: nx4 list of quaternions (w,x,y,z format)
        :param poses_se3: list of SE(3) poses
        :param meta: optional metadata
        """
        if (positions_xyz is None
                or orientations_quat_wxyz is None) and poses_se3 is None:
            raise TrajectoryException("must provide at least positions_xyz "
                                      "& orientations_quat_wxyz or poses_se3")
        if positions_xyz is not None:
            self._positions_xyz = np.array(positions_xyz)
        if orientations_quat_wxyz is not None:
            self._orientations_quat_wxyz = np.array(orientations_quat_wxyz)
        if poses_se3 is not None:
            self._poses_se3 = poses_se3
        if self.num_poses == 0:
            raise TrajectoryException("pose data is empty")
        self.meta = {} if meta is None else meta
        self._projected = False

    def __str__(self) -> str:
        return "{} poses, {:.3f}m path length".format(self.num_poses,
                                                      self.path_length)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PosePath3D):
            return False
        if not self.num_poses == other.num_poses:
            return False
        equal = True
        equal &= all([
            np.allclose(p1, p2)
            for p1, p2 in zip(self.poses_se3, other.poses_se3)
        ])
        equal &= (np.allclose(self.orientations_quat_wxyz,
                              other.orientations_quat_wxyz)
                  or np.allclose(self.orientations_quat_wxyz,
                                 -other.orientations_quat_wxyz))
        equal &= np.allclose(self.positions_xyz, other.positions_xyz)
        return equal

    def __ne__(self, other: object) -> bool:
        return not self == other

    @property
    def positions_xyz(self) -> np.ndarray:
        if not hasattr(self, "_positions_xyz"):
            assert hasattr(self, "_poses_se3")
            self._positions_xyz = np.array([p[:3, 3] for p in self._poses_se3])
        return self._positions_xyz

    @property
    def distances(self) -> np.ndarray:
        return geometry.accumulated_distances(self.positions_xyz)

    @property
    def orientations_quat_wxyz(self) -> np.ndarray:
        if not hasattr(self, "_orientations_quat_wxyz"):
            assert hasattr(self, "_poses_se3")
            self._orientations_quat_wxyz \
                = np.array(
                    [tr.quaternion_from_matrix(p)
                     for p in self._poses_se3])
        return self._orientations_quat_wxyz


    def to_euler_angle(self, rot):
        """
        Convert a quaternion into Euler angles (roll, pitch, yaw).
        The input quaternion is assumed to be in the form [w, x, y, z].

        Args:
            q (list or np.array): Quaternion [w, x, y, z]

        Returns:
            tuple: Euler angles (roll, pitch, yaw) in degrees
        """
        q = R.from_matrix(rot[:3, :3]).as_quat()
        x, y, z, w = q
        # return x, y, z

        # Roll (x-axis rotation)
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp) * 180 / math.pi

        # Pitch (y-axis rotation)
        sinp = 2.0 * (w * y - z * x)
        if abs(sinp) >= 1:
            pitch = math.copysign(math.pi / 2, sinp) * 180 / math.pi  # Use 90 degrees if out of range
        else:
            pitch = math.asin(sinp) * 180 / math.pi

        # Yaw (z-axis rotation)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp) * 180 / math.pi
        
        # Adjust yaw to be in [0, 360) degrees
        if yaw < 0:
            yaw += 360.0

        return roll*math.pi/180, pitch*math.pi/180, yaw*math.pi/180
    
    
    def to_euler_angle_with_constraints(self, rot):
        """
        Convert a quaternion into Euler angles (roll, pitch, yaw).
        The input quaternion is assumed to be in the form [w, x, y, z].

        Args:
            rot (np.array): 4x4 rotation matrix

        Returns:
            tuple: Euler angles (roll, pitch, yaw) in radians
        """
        q = R.from_matrix(rot[:3, :3]).as_quat()
        x, y, z, w = q

        # Roll (x-axis rotation)
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        # Pitch (y-axis rotation) - constrained within -90 <= pitch <= 90
        sinp = 2.0 * (w * y - z * x)
        pitch = math.asin(max(-1.0, min(1.0, sinp)))  # Ensuring asin input is within valid range

        # Yaw (z-axis rotation)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        # Convert the angles from radians to degrees
        roll_deg = math.degrees(roll)
        pitch_deg = math.degrees(pitch)
        yaw_deg = math.degrees(yaw)

        # Apply constraints
        # Roll: Ensure it is within -180 < roll <= 180
        if roll_deg > 180:
            roll_deg -= 360
        elif roll_deg <= -180:
            roll_deg += 360

        # Yaw: Ensure it is within -180 < yaw <= 180
        if yaw_deg > 180:
            yaw_deg -= 360
        elif yaw_deg <= -180:
            yaw_deg += 360

        return roll_deg*math.pi/180, pitch_deg*math.pi/180, yaw_deg*math.pi/180

    
    def to_euler_angle_without_constraint(self, rot):
        """
        Convert a quaternion into Euler angles (roll, pitch, yaw).
        The input quaternion is assumed to be in the form [w, x, y, z].

        Args:
            rot (np.array): 4x4 rotation matrix

        Returns:
            tuple: Euler angles (roll, pitch, yaw) in radians
        """
        q = R.from_matrix(rot[:3, :3]).as_quat()
        x, y, z, w = q

        # Roll (x-axis rotation)
        sinr_cosp = 2.0 * (w * x + y * z)
        cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
        roll = math.atan2(sinr_cosp, cosr_cosp)
        # Pitch (y-axis rotation) - without constraints
        pitch = math.atan2(2.0 * (w * y - z * x), 1.0 - 2.0 * (y * y + x * x))

        # Yaw (z-axis rotation)
        siny_cosp = 2.0 * (w * z + x * y)
        cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
        yaw = math.atan2(siny_cosp, cosy_cosp)

        # Adjust yaw to be in [0, 2*pi) radians
        if yaw < 0:
            yaw += 2 * math.pi

        return roll, pitch, yaw

    def get_orientations_euler(self, axes="sxyz") -> np.ndarray:
        if hasattr(self, "_poses_se3"):
            return np.array(
                [tr.euler_from_matrix(p, axes=axes) for p in self._poses_se3])
            # print("new method")
            # return np.array([
            #     R.from_matrix(q[:3, :3]).as_euler("xyz", degrees=False)
            #     for q in self._poses_se3
            # ])
            # return np.array([
            #     self.to_euler_angle(q)
            #     # self.to_euler_angle_with_constraints(q)
            #     for q in self._poses_se3
            # ])
        assert hasattr(self, "_orientations_quat_wxyz")
        return np.array([
            tr.euler_from_quaternion(q, axes=axes)
            for q in self._orientations_quat_wxyz
        ])

    @property
    def poses_se3(self) -> typing.Sequence[np.ndarray]:
        if not hasattr(self, "_poses_se3"):
            assert hasattr(self, "_positions_xyz")
            assert hasattr(self, "_orientations_quat_wxyz")
            self._poses_se3 \
                = xyz_quat_wxyz_to_se3_poses(self.positions_xyz,
                                             self.orientations_quat_wxyz)
        return self._poses_se3

    @property
    def num_poses(self) -> int:
        if hasattr(self, "_poses_se3"):
            return len(self._poses_se3)
        else:
            return self.positions_xyz.shape[0]

    @property
    def path_length(self) -> float:
        """
        calculates the path length (arc-length)
        :return: path length in meters
        """
        return float(geometry.arc_len(self.positions_xyz))

    def transform(self, t: np.ndarray, right_mul: bool = False,
                  propagate: bool = False) -> None:
        """
        apply a left or right multiplicative transformation to the whole path
        :param t: a 4x4 transformation matrix (e.g. SE(3) or Sim(3))
        :param right_mul: whether to apply it right-multiplicative or not
        :param propagate: whether to propagate drift with RHS transformations
        """
        if right_mul and not propagate:
            # Transform each pose individually.
            self._poses_se3 = [np.dot(p, t) for p in self.poses_se3]
        elif right_mul and propagate:
            # Transform each pose and propagate resulting drift to the next.
            ids = np.arange(0, self.num_poses, 1)
            rel_poses = [
                lie.relative_se3(self.poses_se3[i], self.poses_se3[j]).dot(t)
                for i, j in zip(ids, ids[1:])
            ]
            self._poses_se3 = [self.poses_se3[0]]
            for i, j in zip(ids[:-1], ids):
                self._poses_se3.append(self._poses_se3[j].dot(rel_poses[i]))
        else:
            self._poses_se3 = [np.dot(t, p) for p in self.poses_se3]
        self._positions_xyz, self._orientations_quat_wxyz \
            = se3_poses_to_xyz_quat_wxyz(self.poses_se3)

    def scale(self, s: float) -> None:
        """
        apply a scaling to the whole path
        :param s: scale factor
        """
        if hasattr(self, "_poses_se3"):
            self._poses_se3 = [
                lie.se3(p[:3, :3], s * p[:3, 3]) for p in self._poses_se3
            ]
        if hasattr(self, "_positions_xyz"):
            self._positions_xyz = s * self._positions_xyz

    def project(self, plane: Plane) -> None:
        """
        Projects the positions and orientations of the path into a plane.
        :param plane: desired plane into which the poses will be projected
        """
        if self._projected:
            raise TrajectoryException("path was already projected once")
        if plane == Plane.XY:
            null_dim = 2  # Z
        elif plane == Plane.XZ:
            null_dim = 1  # Y
        elif plane == Plane.YZ:
            null_dim = 0  # X
        else:
            raise TrajectoryException(f"unknown projection plane {plane}")

        # Project poses and rotations (forcing to angle around normal).
        rotation_axis = np.zeros(3)
        rotation_axis[null_dim] = 1
        for pose in self.poses_se3:
            pose[null_dim, 3] = 0
            angle_axis = rotation_axis * tr.euler_from_matrix(
                pose[:3, :3], "sxyz")[null_dim]
            pose[:3, :3] = lie.so3_exp(angle_axis)

        # Flush cached data that will be regenerated on demand via @property.
        if hasattr(self, "_positions_xyz"):
            del self._positions_xyz
        if hasattr(self, "_orientations_quat_wxyz"):
            del self._orientations_quat_wxyz
        self._projected = True



    # def average_transforms(self, transforms):
    #     # Separate rotations and translations
    #     rotations = [R.from_matrix(t[:3, :3]) for t in transforms]
    #     translations = [t[:3, 3] for t in transforms]

    #     # Interpolate rotations using SLERP
    #     mean_rotation = self.slerp_rotations(rotations)
    #     # Average quaternions and translations
    #     # mean_quat = R.from_quat(np.mean([r.as_quat() for r in rotations], axis=0)).as_matrix()
    #     mean_translation = np.mean(translations, axis=0)

    #     # Combine into a single transformation matrix
    #     mean_transform = np.eye(4)
    #     mean_transform[:3, :3] = mean_rotation.as_matrix()
    #     mean_transform[:3, 3] = mean_translation

    #     return mean_transform

    def log_map_se3(self, T):
        """Convert an SE3 transformation to its se3 representation using the log map."""
        # Extract rotation matrix and translation vector
        R_mat = T[:3, :3]
        t_vec = T[:3, 3]
        
        # Convert rotation matrix to angle-axis form (rotation vector)
        r = R.from_matrix(R_mat)
        theta = np.linalg.norm(r.as_rotvec())
        
        # If there's a significant rotation, normalize the rotation vector
        if theta > np.finfo(float).eps:
            omega = r.as_rotvec() / theta
            A = np.sin(theta) / theta
            B = (1 - np.cos(theta)) / (theta**2)
            C = (1 - A) / (theta**2)
            V_inv = np.eye(3) - 0.5 * r.as_rotvec().reshape(3, 1) @ omega.reshape(1, 3) + (1 - A / (2 * B)) * (omega.reshape(3, 1) @ omega.reshape(1, 3))
            v = V_inv @ t_vec
            se3_vec = np.concatenate((theta * omega, v))
        else:
            # For small rotations, treat as identity rotation
            se3_vec = np.concatenate((r.as_rotvec(), t_vec))
        
        return se3_vec

    def exp_map_se3(self, se3_vec):
        """Convert an se3 vector back to an SE3 transformation using the exp map."""
        omega = se3_vec[:3]
        v = se3_vec[3:]
        theta = np.linalg.norm(omega)
        
        # If there's a significant rotation, compute the rotation matrix using Rodrigues' formula
        if theta > np.finfo(float).eps:
            omega_normalized = omega / theta
            R_mat = R.from_rotvec(omega).as_matrix()
            A = np.sin(theta) / theta
            B = (1 - np.cos(theta)) / (theta**2)
            V = np.eye(3) + B * np.cross(np.eye(3), omega_normalized) + (1 - A / B) * (omega_normalized.reshape(3, 1) @ omega_normalized.reshape(1, 3))
            t_vec = V @ v
        else:
            # For small rotations, treat as identity rotation
            R_mat = np.eye(3)
            t_vec = v
        
        # Combine rotation and translation into SE3 transformation
        T = np.eye(4)
        T[:3, :3] = R_mat
        T[:3, 3] = t_vec
        
        return T

    def average_se3_transforms(self, transforms):
        se3_vectors = np.array([self.log_map_se3(T) for T in transforms])
        mean_se3_vector = np.mean(se3_vectors, axis=0)
        mean_transform = self.exp_map_se3(mean_se3_vector)
        return mean_transform


    def compute_relative_transforms(self, poses1, poses2, fraction=0.6):
        # Determine the number of poses to use based on the fraction
        num_poses1 = int(len(poses1) * fraction)
        # num_poses2 = int(len(poses2) * fraction)
        # print("num_pose", len(poses1), len(poses2))
        # print("size1:", len(poses1), len(poses2))

        # for i in range(num_poses1):

        relative_transforms = []
        
        for i in np.arange(0, 1, 1):
            traj_origin = poses1[i]
            traj_ref_origin = poses2[i]
            to_ref_origin = np.dot(lie.se3_inverse(traj_origin), traj_ref_origin)
            relative_transforms.append(to_ref_origin)

        return self.average_se3_transforms(relative_transforms)


    def align(self, traj_ref: 'PosePath3D', correct_scale: bool = False,
              correct_only_scale: bool = False,
              n: float = -1.0) -> geometry.UmeyamaResult:
        """
        align to a reference trajectory using Umeyama alignment
        :param traj_ref: reference trajectory
        :param correct_scale: set to True to adjust also the scale
        :param correct_only_scale: set to True to correct the scale, but not the pose
        :param n: the number of poses to use, counted from the start (default: all)
        :return: the result parameters of the Umeyama algorithm
        """
        with_scale = correct_scale or correct_only_scale
        align_number = int(n * traj_ref.positions_xyz.shape[0])
        if correct_only_scale:
            logger.debug("Correcting scale...")
        else:
            logger.debug("Aligning using Umeyama's method..." +
                         (" (with scale correction)" if with_scale else ""))
        if n == -1.0:
            r_a, t_a, s = geometry.umeyama_alignment(self.positions_xyz.T,
                                                     traj_ref.positions_xyz.T,
                                                     with_scale)
        else:
            r_a, t_a, s = geometry.umeyama_alignment(
                self.positions_xyz[:align_number, :].T, traj_ref.positions_xyz[:align_number, :].T,
                with_scale)

        if not correct_only_scale:
            logger.debug("Rotation of alignment:\n{}"
                         "\nTranslation of alignment:\n{}".format(r_a, t_a))
        logger.debug("Scale correction: {}".format(s))

        # print("r t", r_a, t_a)
        if correct_only_scale:
            self.scale(s)
        elif correct_scale:
            self.scale(s)
            self.transform(lie.se3(r_a, t_a))
            traj_origin0 = self.poses_se3[0]
            traj_ref_origin0 = traj_ref.poses_se3[0]
            to_ref_origin0 = np.dot(lie.se3_inverse(traj_origin0), traj_ref_origin0)
            to_ref_origin_so3 = lie.so3_from_se3(to_ref_origin0)
            to_ref_origin_se3 = lie.se3(to_ref_origin_so3)
            self.transform(to_ref_origin_se3, True)
        else:
            self.transform(lie.se3(r_a, t_a))
            
            traj_origin0 = self.poses_se3[0]
            traj_ref_origin0 = traj_ref.poses_se3[0]
            to_ref_origin0 = np.dot(lie.se3_inverse(traj_origin0), traj_ref_origin0)
            # to_ref_origin[0, 3] = 0
            # to_ref_origin[1, 3] = 0
            # to_ref_origin[2, 3] = 0
            to_ref_origin_so3 = lie.so3_from_se3(to_ref_origin0)
            to_ref_origin_se3 = lie.se3(to_ref_origin_so3)
            self.transform(to_ref_origin_se3, True)

            # avg_transform = self.compute_relative_transforms(self.poses_se3, traj_ref.poses_se3)
            # # print("to_ref_origin_se3", to_ref_origin_se3)
            # # print("avg_transform", avg_transform)
            # self.transform(avg_transform, True)

        return r_a, t_a, s
    
    def align_tran(self, traj_ref: 'PosePath3D', correct_scale: bool = False,
              correct_only_scale: bool = False,
              n: float = -1.0) -> geometry.UmeyamaResult:
        """
        align to a reference trajectory using Umeyama alignment
        :param traj_ref: reference trajectory
        :param correct_scale: set to True to adjust also the scale
        :param correct_only_scale: set to True to correct the scale, but not the pose
        :param n: the number of poses to use, counted from the start (default: all)
        :return: the result parameters of the Umeyama algorithm
        """
        # with_scale = correct_scale or correct_only_scale
        # print("shape:", traj_ref.positions_xyz.shape)
        # align_number = int(n * traj_ref.positions_xyz.shape[0])
        # if correct_only_scale:
        #     logger.debug("Correcting scale...")
        # else:
        #     logger.debug("Aligning using Umeyama's method..." +
        #                  (" (with scale correction)" if with_scale else ""))
        # if n == -1.0:
        #     r_a, t_a, s = geometry.umeyama_alignment(self.positions_xyz.T,
        #                                              traj_ref.positions_xyz.T,
        #                                              with_scale)
        # else:
        #     r_a, t_a, s = geometry.umeyama_alignment(
        #         self.positions_xyz[:align_number, :].T, traj_ref.positions_xyz[:align_number, :].T,
        #         with_scale)

        # if not correct_only_scale:
        #     logger.debug("Rotation of alignment:\n{}"
        #                  "\nTranslation of alignment:\n{}".format(r_a, t_a))
        # logger.debug("Scale correction: {}".format(s))

        r_a = np.identity(3)
        t_a = -self.positions_xyz[0]
        if correct_only_scale:
            self.scale(1)
        elif correct_scale:
            self.scale(1)
            self.transform(lie.se3(r_a, t_a))
        else:
            self.transform(lie.se3(r_a, t_a))

        return r_a, t_a, 1

    def align_origin(self, traj_ref: 'PosePath3D') -> np.ndarray:
        """
        align the origin to the origin of a reference trajectory
        :param traj_ref: reference trajectory
        :return: the used transformation
        """
        if self.num_poses == 0 or traj_ref.num_poses == 0:
            raise TrajectoryException("can't align an empty trajectory...")
        traj_origin = self.poses_se3[0]
        traj_ref_origin = traj_ref.poses_se3[0]
        to_ref_origin = np.dot(traj_ref_origin, lie.se3_inverse(traj_origin))
        logger.debug(
            "Origin alignment transformation:\n{}".format(to_ref_origin))
        self.transform(to_ref_origin)

        to_ref_origin0 = np.dot(lie.se3_inverse(traj_origin), traj_ref_origin)
        to_ref_origin_so3 = lie.so3_from_se3(to_ref_origin0)
        to_ref_origin_se3 = lie.se3(to_ref_origin_so3)
        self.transform(to_ref_origin_se3, True)
    
        return to_ref_origin

    def reduce_to_ids(
            self, ids: typing.Union[typing.Sequence[int], np.ndarray]) -> None:
        """
        reduce the elements to the ones specified in ids
        :param ids: list of integer indices
        """
        if hasattr(self, "_positions_xyz"):
            self._positions_xyz = self._positions_xyz[ids]
        if hasattr(self, "_orientations_quat_wxyz"):
            self._orientations_quat_wxyz = self._orientations_quat_wxyz[ids]
        if hasattr(self, "_poses_se3"):
            self._poses_se3 = [self._poses_se3[idx] for idx in ids]

    def downsample(self, num_poses: int) -> None:
        """
        Downsample the trajectory to the specified number of poses
        with a simple evenly spaced sampling.
        Does nothing if the trajectory already has less or equal poses.
        :param num_poses: number of poses to keep
        """
        if self.num_poses <= num_poses:
            return
        if self.num_poses < 2 or num_poses < 2:
            raise TrajectoryException("can't downsample to less than 2 poses")
        ids = np.linspace(0, self.num_poses - 1, num_poses, dtype=int)
        self.reduce_to_ids(ids)

    def motion_filter(self, distance_threshold: float, angle_threshold: float,
                      degrees: bool = False) -> None:
        """
        Filters the trajectory by its motion if either the accumulated distance
        or rotation angle is exceeded.
        :param distance_threshold: the distance threshold in meters
        :param angle_threshold: the angle threshold in radians
                                (or degrees if degrees=True)
        :param degrees: set to True if angle_threshold is in degrees
        """
        filtered_ids = filters.filter_by_motion(self.poses_se3,
                                                distance_threshold,
                                                angle_threshold, degrees)
        self.reduce_to_ids(filtered_ids)

    def check(self) -> typing.Tuple[bool, dict]:
        """
        checks if the data is valid
        :return: True/False, dictionary with some detailed infos
        """
        if self.num_poses == 0:
            return True, {}
        same_len = self.positions_xyz.shape[0] \
            == self.orientations_quat_wxyz.shape[0] \
            == len(self.poses_se3)
        se3_valid = all([lie.is_se3(p) for p in self.poses_se3])
        norms = np.linalg.norm(self.orientations_quat_wxyz, axis=1)
        quat_normed = np.allclose(norms, np.ones(norms.shape))
        valid = same_len and se3_valid and quat_normed
        details = {
            "array shapes": "ok"
            if same_len else "invalid (lists must have same length)",
            "SE(3) conform": "yes"
            if se3_valid else "no (poses are not valid SE(3) matrices)",
            "quaternions": "ok"
            if quat_normed else "invalid (must be unit quaternions)"
        }
        return valid, details

    def get_infos(self) -> dict:
        """
        :return: dictionary with some infos about the path
        """
        return {
            "nr. of poses": self.num_poses,
            "path length (m)": self.path_length,
            "pos_start (m)": self.positions_xyz[0],
            "pos_end (m)": self.positions_xyz[-1]
        }

    def get_statistics(self) -> dict:
        if self.num_poses < 2:
            return {}
        return {}  # no idea yet


class PoseTrajectory3D(PosePath3D, object):
    """
    a PosePath with temporal information
    """
    def __init__(
            self, positions_xyz: typing.Optional[np.ndarray] = None,
            orientations_quat_wxyz: typing.Optional[np.ndarray] = None,
            timestamps: typing.Optional[np.ndarray] = None,
            poses_se3: typing.Optional[typing.Sequence[np.ndarray]] = None,
            meta: typing.Optional[dict] = None):
        """
        :param timestamps: optional nx1 list of timestamps
        """
        super(PoseTrajectory3D,
              self).__init__(positions_xyz, orientations_quat_wxyz, poses_se3,
                             meta)
        # this is a bit ugly...
        if timestamps is None:
            raise TrajectoryException("no timestamps provided")
        self.timestamps = np.array(timestamps)

    def __str__(self) -> str:
        s = super(PoseTrajectory3D, self).__str__()
        return s + ", {:.3f}s duration".format(self.timestamps[-1] -
                                               self.timestamps[0])

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PoseTrajectory3D):
            return False
        if not self.num_poses == other.num_poses:
            return False
        equal = super(PoseTrajectory3D, self).__eq__(other)
        equal &= np.allclose(self.timestamps, other.timestamps)
        return equal

    def __ne__(self, other: object) -> bool:
        return not self == other

    @property
    def speeds(self) -> np.ndarray:
        """
        :return: array with speed of motion between poses
        """
        if self.num_poses < 2:
            return np.array([])
        return np.array([
            calc_speed(self.positions_xyz[i], self.positions_xyz[i + 1],
                       self.timestamps[i], self.timestamps[i + 1])
            for i in range(len(self.positions_xyz) - 1)
        ])

    def reduce_to_ids(
            self, ids: typing.Union[typing.Sequence[int], np.ndarray]) -> None:
        super(PoseTrajectory3D, self).reduce_to_ids(ids)
        self.timestamps = self.timestamps[ids]

    def reduce_to_time_range(self,
                             start_timestamp: typing.Optional[float] = None,
                             end_timestamp: typing.Optional[float] = None):
        """
        Removes elements with timestamps outside of the specified time range.
        :param start_timestamp: any data with lower timestamp is removed
                                if None: current start timestamp
        :param end_timestamp: any data with larger timestamp is removed
                              if None: current end timestamp
        """
        if self.num_poses == 0:
            raise TrajectoryException("trajectory is empty")
        if start_timestamp is None:
            start_timestamp = self.timestamps[0]
        if end_timestamp is None:
            end_timestamp = self.timestamps[-1]
        if start_timestamp > end_timestamp:
            raise TrajectoryException(
                "start_timestamp is greater than end_timestamp "
                "({} > {})".format(start_timestamp, end_timestamp))
        ids = np.where(
            np.logical_and(self.timestamps >= start_timestamp,
                           self.timestamps <= end_timestamp))[0]
        self.reduce_to_ids(ids)

    def check(self) -> typing.Tuple[bool, dict]:
        if self.num_poses == 0:
            return True, {}
        valid, details = super(PoseTrajectory3D, self).check()
        len_stamps_valid = (len(self.timestamps) == len(self.positions_xyz))
        valid &= len_stamps_valid
        details["nr. of stamps"] = "ok" if len_stamps_valid else "wrong"
        stamps_ascending = bool(
            np.all(np.sort(self.timestamps) == self.timestamps))
        stamps_ascending &= np.unique(self.timestamps).size == len(
            self.timestamps)
        valid &= stamps_ascending
        if stamps_ascending:
            details["timestamps"] = "ok"
        else:
            details["timestamps"] = "wrong, not ascending or duplicates"
        return valid, details

    def get_infos(self) -> dict:
        """
        :return: dictionary with some infos about the trajectory
        """
        infos = super(PoseTrajectory3D, self).get_infos()
        infos["duration (s)"] = self.timestamps[-1] - self.timestamps[0]
        infos["t_start (s)"] = self.timestamps[0]
        infos["t_end (s)"] = self.timestamps[-1]
        return infos

    def get_statistics(self) -> dict:
        """
        :return: dictionary with some statistics of the trajectory
        """
        if self.num_poses < 2:
            return {}
        stats = super(PoseTrajectory3D, self).get_statistics()
        speeds = self.speeds
        vmax = speeds.max()
        vmin = speeds.min()
        vmean = speeds.mean()
        stats.update({
            "v_max (m/s)": vmax,
            "v_min (m/s)": vmin,
            "v_avg (m/s)": vmean,
            "v_max (km/h)": vmax * 3.6,
            "v_min (km/h)": vmin * 3.6,
            "v_avg (km/h)": vmean * 3.6
        })
        return stats


class Trajectory(PoseTrajectory3D):
    pass  # TODO compat


def calc_speed(xyz_1: np.ndarray, xyz_2: np.ndarray, t_1: float,
               t_2: float) -> float:
    """
    :param xyz_1: position at timestamp 1
    :param xyz_2: position at timestamp 2
    :param t_1: timestamp 1
    :param t_2: timestamp 2
    :return: speed in m/s
    """
    if (t_2 - t_1) <= 0:
        raise TrajectoryException("bad timestamps: " + str(t_1) + " & " +
                                  str(t_2))
    return float(np.linalg.norm(xyz_2 - xyz_1) / (t_2 - t_1))


def calc_angular_speed(p_1: np.ndarray, p_2: np.ndarray, t_1: float,
                       t_2: float, degrees: bool = False) -> float:
    """
    :param p_1: pose at timestamp 1
    :param p_2: pose at timestamp 2
    :param t_1: timestamp 1
    :param t_2: timestamp 2
    :param degrees: set to True to return deg/s
    :return: speed in rad/s
    """
    if (t_2 - t_1) <= 0:
        raise TrajectoryException("bad timestamps: " + str(t_1) + " & " +
                                  str(t_2))
    angle_1 = lie.so3_log(p_1[:3, :3], degrees)
    angle_2 = lie.so3_log(p_2[:3, :3], degrees)
    return (angle_2 - angle_1) / (t_2 - t_1)


def xyz_quat_wxyz_to_se3_poses(
        xyz: np.ndarray, quat: np.ndarray) -> typing.Sequence[np.ndarray]:
    poses = [
        lie.se3(lie.so3_from_se3(tr.quaternion_matrix(quat)), xyz)
        for quat, xyz in zip(quat, xyz)
    ]
    return poses


def se3_poses_to_xyz_quat_wxyz(
    poses: typing.Sequence[np.ndarray]
) -> typing.Tuple[np.ndarray, np.ndarray]:
    xyz = np.array([pose[:3, 3] for pose in poses])
    quat_wxyz = np.array([tr.quaternion_from_matrix(pose) for pose in poses])
    return xyz, quat_wxyz


def merge(trajectories: typing.Sequence[PoseTrajectory3D]) -> PoseTrajectory3D:
    """
    Merges multiple trajectories into a single, timestamp-sorted one.
    :param trajectories: list of PoseTrajectory3D objects
    :return: merged PoseTrajectory3D
    """
    merged_stamps = np.concatenate([t.timestamps for t in trajectories])
    merged_xyz = np.concatenate([t.positions_xyz for t in trajectories])
    merged_quat = np.concatenate(
        [t.orientations_quat_wxyz for t in trajectories])
    order = merged_stamps.argsort()
    merged_stamps = merged_stamps[order]
    merged_xyz = merged_xyz[order]
    merged_quat = merged_quat[order]
    return PoseTrajectory3D(merged_xyz, merged_quat, merged_stamps)
