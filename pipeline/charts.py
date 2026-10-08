"""Square (1080x1080) chart images for the LinkedIn carousel."""
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

INK = "#1A2238"       # titles and values
MUTED = "#6B7280"     # axis labels, footnote
GRID = "#E5E7EB"
BAR = "#24407A"       # single-series bar colour (swap for Densight brand navy if different)
ACCENT = "#E8833A"    # highlights the current week on the trend chart
BG = "#FFFFFF"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.edgecolor": GRID,
    "axes.labelcolor": MUTED,
    "xtick.color": MUTED,
    "ytick.color": INK,
})


def _short(name, n=24):
    name = re.sub(r"\s*\((pvt|private)\)\s*|\s+(pvt\.?|private)?\s*(ltd\.?|limited)$", " ", str(name), flags=re.I).strip()
    return name if len(name) <= n else name[: n - 1].rstrip() + "…"


def _frame(title, subtitle, footnote):
    fig = plt.figure(figsize=(10.8, 10.8), dpi=100, facecolor=BG)
    fig.text(0.06, 0.94, title, fontsize=30, fontweight="bold", color=INK, va="top")
    fig.text(0.06, 0.875, subtitle, fontsize=17, color=MUTED, va="top")
    fig.text(0.06, 0.035, footnote, fontsize=12, color=MUTED)
    ax = fig.add_axes([0.36, 0.10, 0.56, 0.72])
    ax.set_facecolor(BG)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    return fig, ax


def hbar(series: pd.Series, path, title, subtitle, footnote, pct=False, top=12):
    s = series.head(top)[::-1]
    fig, ax = _frame(title, subtitle, footnote)
    if s.empty:
        ax.text(0.5, 0.5, "No data this week", ha="center", va="center", fontsize=20, color=MUTED,
                transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
    else:
        ax.barh(range(len(s)), s.values, height=0.62, color=BAR)
        ax.set_yticks(range(len(s)))
        ax.set_yticklabels([_short(i) for i in s.index], fontsize=16)
        ax.tick_params(axis="y", length=0, pad=10)
        ax.xaxis.grid(True, color=GRID, linewidth=1)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", labelsize=12)
        mx = s.max() or 1
        ax.set_xlim(0, mx * 1.15)
        slots = max(len(s), 8)  # keep bars the same thickness when there are only a few
        ax.set_ylim(len(s) - 0.5 - slots, len(s) - 0.5)
        for i, v in enumerate(s.values):
            label = f"{v:.0f}%" if pct else f"{int(v)}"
            ax.text(v + mx * 0.015, i, label, va="center", fontsize=15, color=INK, fontweight="bold")
    fig.savefig(path, facecolor=BG)
    plt.close(fig)


def trend(weekly: pd.Series, path, title, subtitle, footnote):
    """weekly: index = week label, values = postings. Last point is the current week."""
    fig, ax = _frame(title, subtitle, footnote)
    ax.set_position([0.10, 0.12, 0.84, 0.68])
    ax.spines["left"].set_visible(True)
    if weekly.empty:
        ax.text(0.5, 0.5, "Trend starts after the first week", ha="center", va="center",
                fontsize=20, color=MUTED, transform=ax.transAxes)
        ax.set_xticks([]); ax.set_yticks([])
    else:
        x = range(len(weekly))
        ax.plot(x, weekly.values, color=BAR, linewidth=2.5, marker="o", markersize=9)
        ax.plot([len(weekly) - 1], [weekly.values[-1]], marker="o", markersize=14, color=ACCENT)
        ax.text(len(weekly) - 1, weekly.values[-1] * 1.06 + 0.5, f"{int(weekly.values[-1])}",
                ha="center", fontsize=18, fontweight="bold", color=INK)
        ax.set_xticks(list(x))
        ax.set_xticklabels(weekly.index, fontsize=12, rotation=45, ha="right")
        ax.set_ylim(0, max(weekly.max() * 1.25, 1))
        ax.yaxis.grid(True, color=GRID, linewidth=1)
        ax.set_axisbelow(True)
        ax.tick_params(axis="y", labelsize=12, colors=MUTED)
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
