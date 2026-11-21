# Changelog

## 1.1.1 (2026-11-21)

- Mac downloads now open on macOS 12 and 13. The 1.1.0 Mac builds picked up a Qt that needs macOS 14, so release builds are pinned to PySide6 6.9.3

## 1.1.0 (2026-11-21)

- Desktop app for macOS and Windows (`tokenwatt app`, or the downloadable builds)
  - Live page: animated gauges for CPU, memory, GPU and power, scrolling charts where every reading can be hovered, Claude token bars and a feed of the latest requests
  - Analysis page: correlation heatmap, per-window scatter plot, with/without Claude comparison, marginal energy, HTML and CSV export
  - Hardware & settings page, recording from the menu bar / system tray
- Claude Code logs are now followed incrementally (only new lines are read), so watching them live stays cheap
- Release workflow builds Apple Silicon, Intel Mac and Windows downloads from a tag
- Fixed a crash on Apple Silicon Macs, where psutil has no `cpu_freq`
- Python 3.10 or newer is now required (3.9 is end-of-life and gone from GitHub's runners)

## 1.0.0 (2026-11-21)

First release.

- `record`: CPU, per-process Claude CPU, RAM, swap, clock, GPU and power sampling into SQLite
- Power from macOS/Linux battery telemetry, Intel RAPL and nvidia-smi, with a load-based estimate as fallback and automatic calibration when some samples are measured
- `import`: Claude Code session logs (incremental, de-duplicated, subagents included) and generic usage CSVs
- `analyze`, `report`, `export`: windowed Spearman/Pearson correlations, active-vs-idle comparison, marginal Wh per token, lag analysis, offline HTML report
