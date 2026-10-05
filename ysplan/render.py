"""Render the stitched plan: rooms, walls with lengths, doors/windows, areas, ceiling heights."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

PALETTE = ["#dbe9f6", "#fde2c8", "#d9f0d3", "#f3d9ec", "#fff3bf", "#e0e0f8", "#d4f1f0", "#f6dcdc"]


def _fmt(v, nd=2, unit=""):
    """Value with its 95% half-width, e.g. '3.21 ±0.03 m', or a lower bound, e.g. '≥9.0 m²'."""
    if v and v.get("status") == "lower_bound":
        return f"≥{v['lower_bound_m2']:.{nd}f}{unit}"
    if not v or v.get("value") is None:
        return "n/a"
    pm = f" ±{1.96 * v['sigma']:.{nd}f}" if v.get("sigma") else ""
    return f"{v['value']:.{nd}f}{pm}{unit}"


def plan(result, path, title=None, cloud=None):
    fig, ax = plt.subplots(figsize=(11, 11))
    if cloud is not None:
        ax.scatter(cloud[:, 0], cloud[:, 1], s=0.02, c="#9a9a9a", alpha=0.5, zorder=0)
    for i, room in enumerate(result["rooms"]):
        poly = np.array(room["polygon"])
        ax.fill(poly[:, 0], poly[:, 1], color=PALETTE[i % len(PALETTE)], zorder=1)
        for w in room["walls"]:
            (x0, y0), (x1, y1) = w["from"], w["to"]
            ax.plot([x0, x1], [y0, y1], color="#222", lw=3 if w["observed_fraction"] > 0.3 else 1.2,
                    ls="-" if w["observed_fraction"] > 0.3 else "--", zorder=3, solid_capstyle="projecting")
            L = w["length_m"]["value"]
            if L >= 0.6:
                c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
                cen = poly.mean(0)
                d = np.array([x1 - x0, y1 - y0]) / L
                nrm = np.array([-d[1], d[0]])
                if np.dot(cen - c, nrm) < 0:
                    nrm = -nrm
                p = c + 0.18 * nrm
                rot = 0 if abs(d[0]) > 0.5 else 90
                ax.text(p[0], p[1], _fmt(w["length_m"]), ha="center", va="center", rotation=rot, fontsize=6.5,
                        color="#333", zorder=5)
        c = np.array(room.get("label_point", poly.mean(0)))
        ch = room["ceiling_height_m"]
        ch_txt = f"ceil {_fmt(ch, unit=' m')}" if ch.get("value") else "ceil not observed"
        ax.text(c[0], c[1], f"{room['id']}\n{_fmt(room['floor_area_m2'], nd=1, unit=' m²')}\n{ch_txt}",
                ha="center", va="center", fontsize=9, weight="bold", zorder=6)
    for o in result["openings"]:
        if o["axis"] == "u":
            xs, ys = [o["from"], o["to"]], [o["line"]] * 2
        else:
            xs, ys = [o["line"]] * 2, [o["from"], o["to"]]
        col = {"door": "#d62728", "opening": "#9467bd", "window": "#1f77b4"}[o["type"]]
        ax.plot(xs, ys, color="white", lw=5, zorder=4, solid_capstyle="butt")
        ax.plot(xs, ys, color=col, lw=2.5, zorder=4.5, solid_capstyle="butt")
        mx, my = np.mean(xs), np.mean(ys)
        ax.text(mx, my, f"{o['id']} {_fmt(o['width_m'])}", fontsize=6.5, color=col, zorder=7,
                ha="center", va="bottom")
    ax.set_aspect("equal")
    ax.grid(alpha=0.15)
    ax.set_xlabel("m")
    ax.set_ylabel("m")
    from matplotlib.lines import Line2D
    ax.legend(handles=[Line2D([], [], color="#222", lw=3, label="wall (observed)"),
                       Line2D([], [], color="#222", lw=1.2, ls="--", label="wall (inferred)"),
                       Line2D([], [], color="#d62728", lw=2.5, label="door"),
                       Line2D([], [], color="#9467bd", lw=2.5, label="opening"),
                       Line2D([], [], color="#1f77b4", lw=2.5, label="window")],
              loc="upper right", fontsize=8)
    title = title or "Stitched floor plan (lengths in m)"
    if not result.get("alignment", {}).get("floor_observed", True):
        title += "\nfloor not seen: no heights, room outlines unreliable (see capture.notes)"
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
