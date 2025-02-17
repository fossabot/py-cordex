import datetime as dt
import json
import os
from warnings import warn

import cf_xarray as cfxr

# import cf_xarray.units
import numpy as np
import pandas as pd
import xarray as xr

# from cf_xarray.units import units

try:
    import cmor
except Exception:
    warn("no python cmor package available, consider installing it")

# trigger download of cmor tables
from cordex import cmor as cxcmor

# import cordex as cx
from .. import cordex_domain

# from .derived import derivator
from .utils import (
    _encode_time,
    _get_cfvarinfo,
    _get_cordex_pole,
    _get_pole,
    _strip_time_cell_method,
)

__all__ = ["cxcmor"]
# from dateutil import relativedelta as reld

options = {"table_prefix": "CORDEX-CMIP6", "exit_control": "CMOR_NORMAL"}


# from ..core import codes

xr.set_options(keep_attrs=True)

# time offsets relative to left labeling for resampling.
# we want the time label to be the center.
loffsets = {
    "3H": dt.timedelta(hours=1, minutes=30),
    "6H": dt.timedelta(hours=3),
    "D": dt.timedelta(hours=12),
}


mapping_table_default = {"time": "time", "X": "rlon", "Y": "rlat"}


time_axis_names = {"point": "time1", "mean": "time"}

units_format = "cf"  # "~P"
# units.define("deg = degree")

# map mip frequencies to pandas frequencies
freq_map = {
    "1hr": "H",
    "1hrPt": "H",
    "3hr": "3H",
    "3hrPt": "3H",
    "6hr": "6H",
    "day": "D",
}

time_units_default = "days since 1950-01-01T00:00:00"
time_dtype = np.double

# Y=2000

units_convert_rules = {
    "mm": (lambda x: x * 1.0 / 86400.0, "kg m-2 s-1"),
    "kg/kg": (lambda x: x, "1"),
}


def set_options(**kwargs):
    for k, v in kwargs.items():
        if k in options:
            options[k] = v
        else:
            raise Exception(f"unkown config option: {k}")


def resample_both_closed(ds, hfreq, op, **kwargs):
    rolling = getattr(ds.rolling(time=hfreq + 1, center=True), op)()
    freq = "{}H".format(hfreq)
    return rolling.resample(time=freq, loffset=0.5 * pd.Timedelta(hfreq, "H")).nearest()


def _resample_op(ds, hfreq, op, **kwargs):
    rolling = getattr(ds.rolling(time=hfreq + 1, center=True), op)()
    freq = "{}H".format(hfreq)
    return rolling.resample(time=freq, loffset=0.5 * pd.Timedelta(hfreq, "H")).nearest()


def _get_loffset(time):
    return loffsets.get(time, None)


def _clear_time_axis(ds):
    """Delete timesteps with NaN arrays"""
    for data_var in ds.data_vars:
        ds = ds.dropna(dim="time", how="all")
    return ds


def _resample(
    ds, time, time_cell_method="point", label="left", time_offset=True, **kwargs
):
    """Resample a variable."""
    # freq = "{}H".format(hfreq)
    if time_cell_method == "point":
        return ds.resample(
            time=time, label=label, **kwargs
        ).nearest()  # .interpolate("nearest") # use as_freq?
    elif time_cell_method == "mean":
        if time_offset is True:
            loffset = _get_loffset(time)
        else:
            loffset = None
        return ds.resample(time=time, label=label, loffset=loffset, **kwargs).mean()
    else:
        raise Exception("unknown time_cell_method: {}".format(time_cell_method))


def _get_bnds(values):
    bnds = [None] * (len(values) + 1)
    bnds[0] = values[0] - (values[1] - values[0]) / 2
    bnds[len(values)] = values[-1] + (values[-1] - values[-2]) / 2
    i = 1
    while i < len(values):
        bnds[i] = values[i] - (values[i] - values[i - 1]) / 2
        i += 1
    return bnds


def _crop_to_cordex_domain(ds, domain):
    domain = cordex_domain(domain)
    # the method=='nearest' approach does not work well with dask
    return ds.sel(
        rlon=slice(domain.rlon.min(), domain.rlon.max()),
        rlat=slice(domain.rlat.min(), domain.rlat.max()),
    )


def _load_table(table):
    cmor.load_table(table)


def _setup(dataset_table, mip_table, grids_table=None, inpath="."):
    if grids_table is None:
        grids_table = f'{options["table_prefix"]}_grids.json'
    cmor.setup(
        inpath,
        set_verbosity=cmor.CMOR_NORMAL,
        netcdf_file_action=cmor.CMOR_REPLACE,
        exit_control=getattr(cmor, options["exit_control"]),
        logfile=None,
    )
    cmor.dataset_json(dataset_table)
    grid_id = cmor.load_table(grids_table)
    table_id = cmor.load_table(mip_table)
    cmor.set_table(table_id)
    return {"grid": grid_id, "mip": table_id}


def _get_time_axis_name(time_cell_method):
    """Get the name of the CMOR time coordinate"""
    return time_axis_names[time_cell_method]


def _define_axes(ds, table_id):
    if "CORDEX_domain" in ds.attrs:
        grid = cordex_domain(ds.attrs["CORDEX_domain"], add_vertices=True)
        lon_vertices = grid.lon_vertices.to_numpy()
        lat_vertices = grid.lat_vertices.to_numpy()
    else:
        lon_vertices = None
        lat_vertices = None

    cmor.set_table(table_id)
    cmorLat = cmor.axis(
        table_entry="grid_latitude",
        coord_vals=ds.rlat.to_numpy(),
        units=ds.rlat.units,
    )
    cmorLon = cmor.axis(
        table_entry="grid_longitude",
        coord_vals=ds.rlon.to_numpy(),
        units=ds.rlon.units,
    )
    cmorGrid = cmor.grid(
        [cmorLat, cmorLon],
        latitude=ds.lat.to_numpy(),
        longitude=ds.lon.to_numpy(),
        latitude_vertices=lat_vertices,
        longitude_vertices=lon_vertices,
    )
    pole = _get_pole(ds)
    pole_dict = {
        "grid_north_pole_latitude": pole.grid_north_pole_latitude,
        "grid_north_pole_longitude": pole.grid_north_pole_longitude,
        "north_pole_grid_longitude": 0.0,
    }
    cmor.set_grid_mapping(
        cmorGrid,
        "rotated_latitude_longitude",
        list(pole_dict.keys()),
        list(pole_dict.values()),
        ["", "", ""],
    )

    return cmorGrid


def _define_time(ds, table_id, time_cell_method=None):
    cmor.set_table(table_id)

    if time_cell_method is None:
        warn("no time_cell_method given, assuming: point")
        time_cell_method = "point"

    # encode time and time bounds
    time_bounds = cfxr.bounds_to_vertices(ds.cf.get_bounds("time"), "bounds")
    time_bounds.encoding = ds.time.encoding
    time_axis_encode = _encode_time(ds.time).to_numpy()
    time_axis_name = _get_time_axis_name(time_cell_method)

    if time_cell_method == "mean":
        time_bounds_encode = _encode_time(time_bounds).to_numpy()
    else:
        time_bounds_encode = None

    return cmor.axis(
        time_axis_name,
        coord_vals=time_axis_encode,
        # cell_bounds=_get_bnds(time_encode.to_numpy()),
        cell_bounds=time_bounds_encode,
        units=ds.time.encoding["units"],
    )


def _define_grid(ds, table_ids, time_cell_method="point"):
    cmorGrid = _define_axes(ds, table_ids["grid"])

    if "time" in ds:
        cmorTime = _define_time(ds, table_ids["mip"], time_cell_method)
    else:
        cmorTime = None

    return cmorTime, cmorGrid


def _cmor_write(da, table_id, cmorTime, cmorGrid, file_name=True):
    cmor.set_table(table_id)
    if cmorTime is None:
        coords = [cmorGrid]
    else:
        coords = [cmorTime, cmorGrid]
    cmor_var = cmor.variable(da.name, da.units, coords)
    cmor.write(cmor_var, da.values)
    return cmor.close(cmor_var, file_name=file_name)


def _units_convert(da, units, format=None):
    import pint_xarray  # noqa

    # rule = units_convert_rules[units]
    # da = rule[0](da)
    # da.attrs["units"] = rule[1]
    if format is None:
        format = units_format
    da_quant = da.pint.quantify()  # ({d:None for d in da.coords})
    da = da_quant.pint.to(units).pint.dequantify(format=units_format)
    # https://github.com/xarray-contrib/cf-xarray/pull/390
    da["rlon"].attrs["units"] = "degrees"
    da["rlat"].attrs["units"] = "degrees"
    return da


def _cf_units_convert(da, table_file, mapping_table={}):
    """Convert units.

    Convert units according to the rules in units_convert_rules dict.
    Maybe metpy can do this also: https://unidata.github.io/MetPy/latest/tutorials/unit_tutorial.html

    """
    with open(table_file) as f:
        table = json.load(f)
    if da.name in mapping_table:
        map_units = mapping_table[da.name].get("units")
        atr_units = da.attrs.get("units")
        if map_units is not None and atr_units is not None and atr_units != map_units:
            warn(
                f"unit [{map_units}] from mapping table differs from units attribute [{da}], assuming [{map_units}] is correct"
            )
            units = map_units
        elif map_units is not None:
            units = map_units
        else:
            units = atr_units
    else:
        units = da.units
    da.attrs["units"] = units
    cf_units = table["variable_entry"][da.name]["units"]
    if units != cf_units:
        warn(
            "converting units {} from input data to CF units {}".format(units, cf_units)
        )
        da = _units_convert(da, cf_units)
        # da = da.pint.to(cf_units)
    return da


# def _convert_cmor_to_resample_frequency(cmor_table):
#    """Convert CMOR table name into resample frequency"""
#    return resample_frequency[cmor_table]


def _get_time_units(ds):
    """Determine time units of dataset"""
    try:
        return ds.time.encoding["units"]
    except Exception:
        return ds.time.units
    return None


def _set_time_encoding(ds, units, orig):
    u = None
    if units is not None:
        if units == "input":
            if "units" in orig.time.encoding:
                u = orig.time.encoding["units"]
            elif "units" in orig.time.attrs:
                u = orig.time.attrs["units"]
        else:
            u = units
    if u is None:
        u = time_units_default
        warn("time units are set to default: {}".format(u))
    ds.time.encoding["units"] = u
    ds.time.encoding["dtype"] = time_dtype
    return ds


def prepare_variable(
    ds,
    out_name,
    mapping_table=None,
    CORDEX_domain=None,
    time_range=None,
    replace_coords=False,
    squeeze=True,
):
    """prepares a variable for cmorization."""
    is_ds = isinstance(ds, xr.Dataset)

    # no mapping table provided, we assume datasets has already correct out_names and units.
    if mapping_table is None:
        try:
            var_ds = ds[[out_name]]
        except Exception:
            raise Exception(
                f"Could not find {out_name} in dataset. Please make sure, variable names and units have CF standard or pass a mapping table."
            )
    else:
        varname = mapping_table[out_name]["varname"]
        # cf_name = varinfo["cf_name"]
        if is_ds is True:
            var_ds = ds[[varname]]  # .to_dataset()
        else:
            var_ds = ds.to_dataset()
        var_ds = var_ds.rename({varname: out_name})
    # remove point coordinates, e.g, height2m
    if squeeze is True:
        var_ds = var_ds.squeeze(drop=True)
    if CORDEX_domain is not None:
        var_ds = _crop_to_cordex_domain(var_ds, CORDEX_domain)
    if replace_coords is True:
        grid = cordex_domain(CORDEX_domain)
        var_ds = var_ds.assign_coords(rlon=grid.rlon, rlat=grid.rlat)
        var_ds = var_ds.assign_coords(lon=grid.lon, lat=grid.lat)
    # var_ds.attrs = ds.attrs
    return var_ds


def _add_time_bounds(ds):
    ds = ds.cf.add_bounds("time")
    ds["time_bounds"].encoding = ds.time.encoding
    return ds


def adjust_frequency(ds, cfvarinfo, input_freq=None):
    if input_freq is None and "time" in ds.coords:
        input_freq = xr.infer_freq(ds.time)
    if input_freq is None:
        warn("could not determine frequency of input data, will assume it is correct.")
        return ds
    freq = freq_map[cfvarinfo["frequency"]]
    if freq != input_freq:
        warn("resampling input data from {} to {}".format(input_freq, freq))
        resample = _resample(
            ds, freq, time_cell_method=_strip_time_cell_method(cfvarinfo)
        )
        return resample
    return ds


def cmorize_variable(
    ds,
    out_name,
    cmor_table,
    dataset_table,
    mapping_table=None,
    grids_table=None,
    inpath=".",
    replace_coords=False,
    allow_units_convert=False,
    allow_resample=False,
    input_freq=None,
    CORDEX_domain=None,
    vertices=None,
    time_units=None,
    outpath=None,
    **kwargs,
):
    """Cmorizes a variable.

    Parameters
    ----------
    ds : xr.Dataset
        Dataset containing at least the variable that should be cmorized.
    out_name: str
        CF out_name of the variable that should be cmorized. The corresponding variable name
        in the dataset is looked up from the mapping_table if provided.
    cmor_table : str
        Filepath to cmor table.
    dataset_table: str
        Filepath to dataset cmor table.
    mapping_table: dict
        Mapping of input variable names and meta data to CF out_name. Required if
        the variable name in the input dataset is not equal to out_name.
    grids_table: str
        Filepath to cmor grids table.
    inpath: str
        Path to cmor tables, if ``inpath == "."``, inpath is the path
        to ``cmor_table``. This is required to find additional cmor tables,
        like ``CMIP6_coordinates``, ``CMIP6_grids`` etc.
    replace_coords: bool
        Replace coordinates from input file and create them from archive
        specifications.
    allow_units_convert: bool
        Allow units to be converted if they do not agree with the
        units in the cmor table. Defaults to ``False`` to make the user aware of having
        correct ``units`` attributes set.
    allow_resample: bool
        Allow to resample temporal data to the frequency required from the cmor table.
        Handles both downsampling and upsampling. Defaults to ``False`` to make users aware
        of the correct frequency input.
    input_freq: str
        The frequency of the input dataset in pandas notation. It ``None`` and the dataset
        contains a time axis, the frequency will be determined automatically using
        ``pandas.infer_freq`` if possible.
    CORDEX_domain: str
        Cordex domain short name. If ``None``, the domain will be determined by the ``CORDEX_domain``
        global attribute if available.
    time_units: str
        Time units of the cmorized dataset (``ISO 8601``).
        If ``None``, time units will be set to default (``"days since 1950-01-01T00:00:00"``).
        If ``time_units='input'``, the original time units of the input dataset are used.
    outpath: str
        Root directory for output (can be either a relative or full path). This will override
        the outpath defined in the dataset cmor input table.
    **kwargs:
        Argumets passed to prepare_variable.

    Returns
    -------
    filename
        Filepath to cmorized file.

    """
    ds = ds.copy()

    if CORDEX_domain is None:
        try:
            CORDEX_domain = ds.CORDEX_domain
        except Exception:
            warn(
                "could not identify CORDEX domain, try to set the 'CORDEX_domain' argument"
            )
    elif "CORDEX_domain" not in ds.attrs:
        ds.attrs["CORDEX_domain"] = CORDEX_domain

    if inpath == ".":
        inpath = os.path.dirname(cmor_table)

    ds_prep = prepare_variable(
        ds,
        out_name,
        CORDEX_domain=CORDEX_domain,
        mapping_table=mapping_table,
        replace_coords=replace_coords,
        **kwargs,
    )

    cfvarinfo = _get_cfvarinfo(out_name, cmor_table)

    if cfvarinfo is None:
        raise Exception("{} not found in {}".format(out_name, cmor_table))
    if "time" in ds:
        if allow_resample is True:
            ds_prep = adjust_frequency(ds_prep, cfvarinfo, input_freq)
        ds_prep = _set_time_encoding(ds_prep, time_units, ds)
        if "time" not in ds.cf.bounds:
            warn("adding time bounds")
            ds_prep = _add_time_bounds(ds_prep)
    # return ds_prep
    pole = _get_pole(ds)

    if pole is None:
        warn("adding pole from archive specs: {}".format(CORDEX_domain))
        pole = _get_cordex_pole(CORDEX_domain)

    ds_prep = xr.merge([ds_prep, pole])
    # return ds_prep
    if allow_units_convert is True:
        ds_prep[out_name] = _cf_units_convert(
            ds_prep[out_name], cmor_table, mapping_table
        )

    table_ids = _setup(
        dataset_table, cmor_table, grids_table=grids_table, inpath=inpath
    )
    time_cell_method = _strip_time_cell_method(cfvarinfo)

    cmorTime, cmorGrid = _define_grid(
        ds_prep, table_ids, time_cell_method=time_cell_method
    )

    return _cmor_write(ds_prep[out_name], table_ids["mip"], cmorTime, cmorGrid)
