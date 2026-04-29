"""IDF pre-processing helpers for geometry CSV and synthetic TOF spectrum output."""

import math
from pathlib import Path
import xml.etree.ElementTree as ET


TOF_LAMBDA_CONVERSION_US_PER_M_ANGSTROM = 252.777
PI4 = 4.0 * math.pi


def local_name(tag: str) -> str:
    """Return an XML tag's local name without namespace."""
    return tag.rsplit("}", 1)[-1]


def attr_f(elem: ET.Element, name: str, default: float = 0.0) -> float:
    """Read a float attribute with a default fallback."""
    value = elem.get(name)
    if value is None:
        return default

    try:
        return float(value)
    except ValueError:
        # Some legacy IDFs encode values like "-90/0" for equivalent "-90".
        if "/" in value:
            return float(value.split("/", 1)[0])
        raise


def v_add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_norm(a: tuple[float, float, float]) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def v_dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def mat_identity() -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def mat_mul(
    a: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    b: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    return (
        (
            a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
            a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
            a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2],
        ),
        (
            a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
            a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
            a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2],
        ),
        (
            a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
            a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
            a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2],
        ),
    )


def mat_vec(
    m: tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    v: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def rotation_matrix(axis: tuple[float, float, float], angle_deg: float):
    """Build a 3D rotation matrix for axis-angle rotation."""
    ax, ay, az = axis
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n == 0.0:
        return mat_identity()

    ax /= n
    ay /= n
    az /= n

    t = math.radians(angle_deg)
    c = math.cos(t)
    s = math.sin(t)
    ic = 1.0 - c

    return (
        (c + ax * ax * ic, ax * ay * ic - az * s, ax * az * ic + ay * s),
        (ay * ax * ic + az * s, c + ay * ay * ic, ay * az * ic - ax * s),
        (az * ax * ic - ay * s, az * ay * ic + ax * s, c + az * az * ic),
    )


def parse_rot_chain(location_elem: ET.Element):
    """Parse nested <rot> elements under a <location> into one rotation matrix."""

    def parse_rot_elem(rot_elem: ET.Element):
        axis = (
            attr_f(rot_elem, "axis-x", 0.0),
            attr_f(rot_elem, "axis-y", 0.0),
            attr_f(rot_elem, "axis-z", 1.0),
        )
        rot_total = rotation_matrix(axis, attr_f(rot_elem, "val", 0.0))
        for child in rot_elem:
            if local_name(child.tag) == "rot":
                rot_total = mat_mul(rot_total, parse_rot_elem(child))
        return rot_total

    rot = mat_identity()
    for child in location_elem:
        if local_name(child.tag) == "rot":
            rot = mat_mul(rot, parse_rot_elem(child))
    return rot


def parse_location(location_elem: ET.Element):
    """Read translation and rotation from a <location> element."""
    pos = (
        attr_f(location_elem, "x", 0.0),
        attr_f(location_elem, "y", 0.0),
        attr_f(location_elem, "z", 0.0),
    )
    rot = parse_rot_chain(location_elem)

    # Some IDFs store a single axis-angle rotation directly on <location>
    # rather than in nested <rot> children.
    if location_elem.get("rot") is not None:
        axis = (
            attr_f(location_elem, "axis-x", 0.0),
            attr_f(location_elem, "axis-y", 0.0),
            attr_f(location_elem, "axis-z", 1.0),
        )
        rot = mat_mul(rot, rotation_matrix(axis, attr_f(location_elem, "rot", 0.0)))

    return pos, rot


def parse_idlist(idlist_elem: ET.Element) -> list[int]:
    """Expand an <idlist> into a concrete ID sequence."""
    ids: list[int] = []
    for child in idlist_elem:
        if local_name(child.tag) != "id":
            continue

        if child.get("val") is not None:
            ids.append(int(child.get("val")))
            continue

        if child.get("start") is not None and child.get("end") is not None:
            start = int(child.get("start"))
            end = int(child.get("end"))
            step = int(child.get("step", "1"))
            if step == 0:
                raise ValueError("Invalid idlist step=0")

            if start <= end:
                ids.extend(range(start, end + 1, abs(step)))
            else:
                ids.extend(range(start, end - 1, -abs(step)))

    return ids


def build_type_and_id_maps(root: ET.Element):
    type_map: dict[str, ET.Element] = {}
    idlists: dict[str, list[int]] = {}

    for child in root:
        tag = local_name(child.tag)
        if tag == "type" and child.get("name"):
            type_map[child.get("name")] = child
        elif tag == "idlist" and child.get("idname"):
            idlists[child.get("idname")] = parse_idlist(child)

    return type_map, idlists


def rectangular_detector_positions(
    type_elem: ET.Element,
    parent_pos: tuple[float, float, float],
    parent_rot,
) -> list[tuple[float, float, float]]:
    """Expand a rectangular_detector type into global pixel center positions."""
    x_pixels = int(type_elem.get("xpixels", "0"))
    y_pixels = int(type_elem.get("ypixels", "0"))
    x_start = attr_f(type_elem, "xstart", 0.0)
    y_start = attr_f(type_elem, "ystart", 0.0)
    x_step = attr_f(type_elem, "xstep", 0.0)
    y_step = attr_f(type_elem, "ystep", 0.0)

    positions: list[tuple[float, float, float]] = []

    # Emit in x-major, y-minor order to align with idfillbyfirst="y" mapping.
    for ix in range(x_pixels):
        x = x_start + ix * x_step
        for iy in range(y_pixels):
            y = y_start + iy * y_step
            local_pos = (x, y, 0.0)
            positions.append(v_add(parent_pos, mat_vec(parent_rot, local_pos)))

    return positions


def rectangular_detector_ids(component_elem: ET.Element, type_elem: ET.Element) -> list[int]:
    """Build detector IDs for rectangular_detector components using idstart/idfillbyfirst layout."""
    if component_elem.get("idstart") is None:
        return []

    start = int(component_elem.get("idstart", "0"))
    fill_first = component_elem.get("idfillbyfirst", "y").strip().lower()
    step = int(component_elem.get("idstep", "1"))

    x_pixels = int(type_elem.get("xpixels", "0"))
    y_pixels = int(type_elem.get("ypixels", "0"))

    default_row_step = (y_pixels if fill_first == "y" else x_pixels) * step
    row_step = int(component_elem.get("idstepbyrow", str(default_row_step)))

    ids: list[int] = []

    if fill_first == "y":
        for ix in range(x_pixels):
            base = start + ix * row_step
            for iy in range(y_pixels):
                ids.append(base + iy * step)
    else:
        for iy in range(y_pixels):
            base = start + iy * row_step
            for ix in range(x_pixels):
                ids.append(base + ix * step)

    return ids


def ids_for_component(
    component_elem: ET.Element,
    component_type: str,
    type_map: dict[str, ET.Element],
    idlists: dict[str, list[int]],
) -> list[int]:
    """Return detector IDs for a component via idlist or rectangular idstart mapping."""
    idlist_name = component_elem.get("idlist")
    if idlist_name is not None:
        return idlists.get(idlist_name, [])

    component_type_elem = type_map.get(component_type)
    if component_type_elem is not None and component_type_elem.get("is") == "rectangular_detector":
        return rectangular_detector_ids(component_elem, component_type_elem)

    return []


def detector_type_names(type_map: dict[str, ET.Element]) -> set[str]:
    names: set[str] = set()
    for name, elem in type_map.items():
        if elem.get("is") == "detector":
            names.add(name)
    return names


def component_children(elem: ET.Element) -> list[ET.Element]:
    return [child for child in elem if local_name(child.tag) == "component"]


def location_children(elem: ET.Element) -> list[ET.Element]:
    return [child for child in elem if local_name(child.tag) == "location"]


def expand_type_positions(
    type_name: str,
    parent_pos: tuple[float, float, float],
    parent_rot,
    type_map: dict[str, ET.Element],
    detector_types: set[str],
    out_positions: list[tuple[float, float, float]],
) -> None:
    """Recursively expand a type into detector leaf global positions."""
    if type_name in detector_types:
        out_positions.append(parent_pos)
        return

    type_elem = type_map.get(type_name)
    if type_elem is None:
        return

    comps = component_children(type_elem)
    if not comps:
        if type_elem.get("is") == "rectangular_detector":
            out_positions.extend(rectangular_detector_positions(type_elem, parent_pos, parent_rot))
        return

    for comp in comps:
        child_type = comp.get("type")
        if child_type is None:
            continue

        locs = location_children(comp)
        if not locs:
            locs = [None]

        for loc in locs:
            if loc is None:
                local_pos = (0.0, 0.0, 0.0)
                local_rot = mat_identity()
            else:
                local_pos, local_rot = parse_location(loc)

            global_pos = v_add(parent_pos, mat_vec(parent_rot, local_pos))
            global_rot = mat_mul(parent_rot, local_rot)
            expand_type_positions(child_type, global_pos, global_rot, type_map, detector_types, out_positions)


def component_global_placements(
    component_elem: ET.Element,
    parent_pos: tuple[float, float, float],
    parent_rot,
) -> list[
    tuple[
        tuple[float, float, float],
        tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
    ]
]:
    """Return all global (position, rotation) placements for a component's location entries."""
    placements: list[
        tuple[
            tuple[float, float, float],
            tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]],
        ]
    ] = []
    locs = location_children(component_elem)
    if not locs:
        locs = [None]

    for loc in locs:
        if loc is None:
            local_pos = (0.0, 0.0, 0.0)
            local_rot = mat_identity()
        else:
            local_pos, local_rot = parse_location(loc)

        global_pos = v_add(parent_pos, mat_vec(parent_rot, local_pos))
        global_rot = mat_mul(parent_rot, local_rot)
        placements.append((global_pos, global_rot))

    return placements


def add_rows_for_ids_and_positions(
    ids: list[int],
    detector_positions: list[tuple[float, float, float]],
    component_type: str,
    sample_pos: tuple[float, float, float],
    beam_vec: tuple[float, float, float],
    beam_norm: float,
    rows: list[tuple[int, float, float, float, float]],
) -> None:
    """Convert detector ids/positions to geometry rows and append to output."""
    if len(detector_positions) != len(ids):
        if detector_positions == [] and all(det_id < 0 for det_id in ids):
            return
        raise ValueError(
            f"ID/position mismatch for component type '{component_type}': "
            f"{len(ids)} ids vs {len(detector_positions)} positions"
        )

    for det_id, det_pos in zip(ids, detector_positions):
        if det_id < 0:
            continue

        sample_to_det = v_sub(det_pos, sample_pos)
        l2 = v_norm(sample_to_det)
        if l2 == 0.0:
            continue

        cos_theta = v_dot(beam_vec, sample_to_det) / (beam_norm * l2)
        cos_theta = max(-1.0, min(1.0, cos_theta))
        theta_deg = math.degrees(math.acos(cos_theta))
        l_total = beam_norm + l2
        theta_rad = math.radians(theta_deg)
        q_matrix_element = PI4 * math.sin(theta_rad / 2.0) * TOF_LAMBDA_CONVERSION_US_PER_M_ANGSTROM * l_total
        rows.append((det_id, l2, theta_deg, l_total, q_matrix_element))


def collect_component_rows(
    component_elem: ET.Element,
    parent_pos: tuple[float, float, float],
    parent_rot,
    type_map: dict[str, ET.Element],
    idlists: dict[str, list[int]],
    detector_types: set[str],
    sample_pos: tuple[float, float, float],
    beam_vec: tuple[float, float, float],
    beam_norm: float,
    rows: list[tuple[int, float, float, float, float]],
) -> None:
    """Recursively collect detector rows from a component tree."""
    component_type = component_elem.get("type")
    if component_type is None:
        return

    for global_pos, global_rot in component_global_placements(component_elem, parent_pos, parent_rot):
        ids = ids_for_component(component_elem, component_type, type_map, idlists)

        if ids:
            detector_positions: list[tuple[float, float, float]] = []
            expand_type_positions(component_type, global_pos, global_rot, type_map, detector_types, detector_positions)
            add_rows_for_ids_and_positions(
                ids=ids,
                detector_positions=detector_positions,
                component_type=component_type,
                sample_pos=sample_pos,
                beam_vec=beam_vec,
                beam_norm=beam_norm,
                rows=rows,
            )
            continue

        type_elem = type_map.get(component_type)
        if type_elem is None:
            continue

        for child in component_children(type_elem):
            collect_component_rows(
                component_elem=child,
                parent_pos=global_pos,
                parent_rot=global_rot,
                type_map=type_map,
                idlists=idlists,
                detector_types=detector_types,
                sample_pos=sample_pos,
                beam_vec=beam_vec,
                beam_norm=beam_norm,
                rows=rows,
            )


def find_component_position(root: ET.Element, component_type: str) -> tuple[float, float, float]:
    """Find a top-level component's location position by type name."""
    for child in root:
        if local_name(child.tag) != "component":
            continue
        if child.get("type") != component_type:
            continue
        for loc in location_children(child):
            pos, _ = parse_location(loc)
            return pos
    return (0.0, 0.0, 0.0)


def parse_l1_distance(root: ET.Element) -> tuple[tuple[float, float, float], tuple[float, float, float], float]:
    """Return source/sample positions and source-to-sample distance L1."""
    source_pos = find_component_position(root, "moderator")
    sample_pos = find_component_position(root, "sample-position")
    beam_vec = v_sub(sample_pos, source_pos)
    l1 = v_norm(beam_vec)
    if l1 == 0.0:
        raise ValueError("Invalid IDF: source and sample overlap")
    return source_pos, sample_pos, l1


def build_detector_geometry(idf_path: Path):
    """Parse IDF and return detector rows with id, L2, theta, Ltotal, and TOF-to-Q matrix element."""
    tree = ET.parse(idf_path)
    root = tree.getroot()
    type_map, idlists = build_type_and_id_maps(root)
    det_types = detector_type_names(type_map)

    source_pos, sample_pos, beam_norm = parse_l1_distance(root)
    beam_vec = v_sub(sample_pos, source_pos)

    rows: list[tuple[int, float, float, float, float]] = []
    top_components = [c for c in root if local_name(c.tag) == "component"]
    for comp in top_components:
        collect_component_rows(
            component_elem=comp,
            parent_pos=(0.0, 0.0, 0.0),
            parent_rot=mat_identity(),
            type_map=type_map,
            idlists=idlists,
            detector_types=det_types,
            sample_pos=sample_pos,
            beam_vec=beam_vec,
            beam_norm=beam_norm,
            rows=rows,
        )

    rows.sort(key=lambda r: r[0])
    return rows


def build_synthetic_tof_spectrum(x_min_us: float, x_max_us: float, bin_width_us: float):
    """Build a simple One Peak-like synthetic TOF spectrum."""
    n_bins = int((x_max_us - x_min_us) / bin_width_us)
    tof_centers = [x_min_us + (i + 0.5) * bin_width_us for i in range(n_bins)]

    center = tof_centers[len(tof_centers) // 2]
    sigma = 0.7
    background = 0.3
    height = 10.0
    y = [background + height * math.exp(-0.5 * ((x - center) / sigma) ** 2) for x in tof_centers]

    return tof_centers, y
