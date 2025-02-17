from warnings import warn

import numpy as np
import xarray as xr
from pyproj import CRS, Transformer

from . import cf

xr.set_options(keep_attrs=True)


def _map_crs(x_stack, y_stack, src_crs, trg_crs=None):
    """coordinate transformation of longitude and latitude"""

    from cartopy import crs as ccrs

    if trg_crs is None:
        trg_crs = ccrs.PlateCarree()
    result = trg_crs.transform_points(src_crs, x_stack, y_stack)
    return result[:, :, 0], result[:, :, 1]


# wrapper function for xarray.apply_ufunc
def map_crs(x, y, src_crs, trg_crs=None):
    """coordinate transformation using cartopy

    Transforms the coordinates x, y from the source crs
    into the target crs using cartopy.

    Parameters
    ----------
    x : float array like
        x coordinate of source crs.
    y : float array like
        y coordinate of source crs.
    src_crs : cartopy.crs
        Source coordinate reference system in which x and y
        are defined.
    trg_crs : cartopy.crs
        Target coordinate reference system into which x and y
        should be transformed. If `None`, `PlateCarree` is used.

    Returns
    -------
    x_map : xr.DataArray
        Projected x coordinate.
    y_map : xr.DataArray
        Projected y coordinate.

    """
    warn(
        "map_crs is deprecated, please use transform_xy instead",
        DeprecationWarning,
        stacklevel=2,
    )
    y_stack, x_stack = xr.broadcast(y, x)
    input_core_dims = 2 * [list(x_stack.dims)] + [[], []]
    output_core_dims = 2 * [list(x_stack.dims)]

    result = xr.apply_ufunc(
        _map_crs,  # first the function
        x_stack,  # now arguments in the order expected by 'interp1_np'
        y_stack,
        src_crs,
        trg_crs,
        input_core_dims=input_core_dims,  # list with one entry per arg
        # [["rlat", "rlon"], ["rlat", "rlon"]],
        output_core_dims=output_core_dims
        # exclude_dims=set(("lat",)),  # dimensions allowed to change size. Must be set!
    )

    result[0].name = "x_map"
    result[1].name = "y_map"

    return result


def _transform(x, y, src_crs, trg_crs):
    """helper function for transforming coordinates"""
    # always_xy=True
    # https://proj.org/faq.html#why-is-the-axis-ordering-in-proj-not-consistent
    transformer = Transformer.from_crs(src_crs, trg_crs, always_xy=True)
    xt, yt = transformer.transform(x, y)
    return xt, yt


def transform(x, y, src_crs, trg_crs=None):
    """Coordinate transformation using pyproj.

    Transforms the coordinates x, y from the source crs
    into a target crs using pyproj.

    Parameters
    ----------
    x : DataArray
        X coordinate.
    y : DataArray
        Y coordinate.
    src_crs : pyproj.CRS
        Source coordinate reference system in which x and y are defined.
    trg_crs : pyproj.CRS
        Target coordinate reference system into which x and y
        should be transformed. If not supplied, ``EPSG:4326`` is the default.

    Returns
    -------
    xt : DataArray
        Transformed x coordinate.
    yt : DataArray
        Transformed y coordinate.

    """
    if trg_crs is None:
        # default target crs
        trg_crs = CRS("EPSG:4326")
    y_stack, x_stack = xr.broadcast(y, x)
    input_core_dims = [x_stack.dims, y_stack.dims] + [[], []]
    output_core_dims = [x_stack.dims, y_stack.dims]

    xt, yt = xr.apply_ufunc(
        _transform,
        x_stack,
        y_stack,
        src_crs,
        trg_crs,
        input_core_dims=input_core_dims,
        output_core_dims=output_core_dims,
    )

    xt.name = "xt"
    yt.name = "yt"
    xt.attrs = {"epsg": trg_crs.to_epsg()}
    yt.attrs = {"epsg": trg_crs.to_epsg()}

    return xt, yt


def transform_coords(ds, src_crs=None, trg_crs=None, trg_dims=None):
    """Transform X and Y coordinates of a Dataset.

    The transformed coordinates will be added to the Dataset.

    Parameters
    ----------
    ds : Dataset or DataArray
        Dataset with input grid.
    src_crs : pyproj.CRS
        Source coordinate reference system in which X and Y are defined.
        If not supplied, a `grid_mapping` variable should be available
        to define the source CRS.
    trg_crs : pyproj.CRS
        Target coordinate reference system into which x and y
        should be transformed. If not supplied, ``EPSG:4326`` is the default.
    trg_dims: list or set
        Names of the output coordinates.

    Returns
    -------
    ds : Dataset or DataArray
        Dataset with transformed coordinates.

    """

    if trg_crs is None:
        # default target crs
        trg_crs = CRS("EPSG:4326")
    if trg_dims is None:
        trg_dims = ("xt", "yt")
    if src_crs is None:
        src_crs = CRS.from_cf(ds.cf["grid_mapping"].attrs)
    x, y = ds.cf["X"], ds.cf["Y"]
    xt, yt = transform(x, y, src_crs, trg_crs)

    return ds.assign_coords({trg_dims[0]: xt, trg_dims[1]: yt})


def rotated_coord_transform(lon, lat, np_lon, np_lat, direction="rot2geo"):
    """Transforms a coordinate into a rotated grid coordinate and vice versa.

    The coordinates have to be given in degree and will be returned in degree.

    Parameters
    ----------
    lon : float array like
        Longitude coordinate.
    lat : float array like
        Latitude coordinate.
    np_lon : float array like
        Longitude coordinate of the rotated north pole.
    np_lat : float array like
        Latitude coordinate of the rotated north pole.
    direction : str
        Direction of the rotation.
        Options are: 'rot2geo' (default) for a transformation to regular
        coordinates from rotated. 'geo2rot' transforms regular coordinates
        to rotated.

    Returns
    -------
    lon_new : array like
        New longitude coordinate.
    lat_new : array like
        New latitude coordinate.
    """
    warn(
        "rotated_coord_transform is deprecated, please use transform_xy instead",
        DeprecationWarning,
        stacklevel=2,
    )
    # Convert degrees to radians
    lon = np.deg2rad(lon)
    lat = np.deg2rad(lat)

    theta = 90.0 - np_lat  # Rotation around y-axis
    phi = np_lon + 180.0  # Rotation around z-axis

    # Convert degrees to radians
    phi = np.deg2rad(phi)
    theta = np.deg2rad(theta)

    # Convert from spherical to cartesian coordinates
    x = np.cos(lon) * np.cos(lat)
    y = np.sin(lon) * np.cos(lat)
    z = np.sin(lat)

    # Regular -> Rotated
    if direction == "geo2rot":
        x_new = (
            np.cos(theta) * np.cos(phi) * x
            + np.cos(theta) * np.sin(phi) * y
            + np.sin(theta) * z
        )
        y_new = -np.sin(phi) * x + np.cos(phi) * y
        z_new = (
            -np.sin(theta) * np.cos(phi) * x
            - np.sin(theta) * np.sin(phi) * y
            + np.cos(theta) * z
        )

    # Rotated -> Regular
    elif direction == "rot2geo":
        phi = -phi
        theta = -theta

        x_new = (
            np.cos(theta) * np.cos(phi) * x
            + np.sin(phi) * y
            + np.sin(theta) * np.cos(phi) * z
        )
        y_new = (
            -np.cos(theta) * np.sin(phi) * x
            + np.cos(phi) * y
            - np.sin(theta) * np.sin(phi) * z
        )
        z_new = -np.sin(theta) * x + np.cos(theta) * z

    # Convert cartesian back to spherical coordinates
    lon_new = np.arctan2(y_new, x_new)
    lat_new = np.arcsin(z_new)

    # Convert radians back to degrees
    lon_new = np.rad2deg(lon_new)
    lat_new = np.rad2deg(lat_new)

    return lon_new, lat_new


def grid_mapping(pollon, pollat, mapping_name=None):
    """creates a grid mapping DataArray object"""
    if mapping_name is None:
        mapping_name = cf.DEFAULT_MAPPING_NCVAR
    da = xr.DataArray(np.zeros((), dtype=np.int32))
    attrs = cf.mapping.copy()
    attrs["grid_north_pole_longitude"] = pollon
    attrs["grid_north_pole_latitude"] = pollat
    da.attrs = attrs
    da.name = mapping_name
    return da
