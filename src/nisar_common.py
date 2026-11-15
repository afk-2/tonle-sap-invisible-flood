"""
nisar_common.py - small shared helpers for reading NISAR data from NASA Earthdata.

- login():            Earthdata login (uses the saved ~/_netrc; never prints credentials)
- with_retry():       retry a network call (the home DNS is flaky: 'getaddrinfo failed')
- granule_info():     (granule name, start time) from a CMR search result
- parse_gcov_name():  split a GCOV granule name into track / direction / frame / mode / ...
- GCOV_GRID:          HDF5 group that holds the GCOV images (HHHH, HVHV, mask, x/yCoordinates, projection)
"""
import sys
import time

GCOV_GRID = "science/LSAR/GCOV/grids/frequencyA"


def parse_gcov_name(name):
    """Split a GCOV granule name into its fields. Returns {} if it does not look like GCOV.

    NISAR_L2_PR_GCOV_023_083_A_008_2005_DHDH_M_20260619T223637_20260619T223711_P05023_N_F_J_001
    parts: 4=cycle 5=track 6=A/D 7=frame 8=mode 9=pols 10=M/A 11=start 12=end 13=CRID
           14=accuracy 15=F(full)/P(partial) 16=location 17=counter
    mode 2005 = 20 MHz (~20 m pixels), 4005 = 40 MHz (~10 m). pols DHDH = HH+HV, SHSH = HH only.
    """
    p = name.split("_")
    if len(p) < 16 or p[3] != "GCOV":
        return {}
    return dict(cycle=p[4], track=p[5], direction=p[6], frame=p[7], mode=p[8],
                pols=p[9], source=p[10], coverage=p[15])


def with_retry(what, fn, *args, tries=6, **kwargs):
    """Call fn(*args, **kwargs), retrying on network errors.
    requests' ConnectionError and fsspec's FileNotFoundError are both OSError subclasses."""
    for attempt in range(1, tries + 1):
        try:
            return fn(*args, **kwargs)
        except OSError as e:
            print(f"  {what}: network error (attempt {attempt}/{tries}): {type(e).__name__}")
            if attempt == tries:
                sys.exit(f"{what}: could not reach NASA Earthdata (network/DNS problem). "
                         "Check your internet connection, VPN or DNS settings and try again.")
            time.sleep(min(3 * attempt, 15))


def login():
    import earthaccess
    # env vars -> .netrc -> asks you
    auth = with_retry("login", earthaccess.login, strategy="all", persist=True)
    if not getattr(auth, "authenticated", False):
        sys.exit("Earthdata login failed. Create a free account at https://urs.earthdata.nasa.gov")
    return earthaccess


def granule_info(g):
    umm = g["umm"]
    name = umm["GranuleUR"]
    try:
        begin = umm["TemporalExtent"]["RangeDateTime"]["BeginningDateTime"]
    except Exception:
        begin = ""
    return name, begin
