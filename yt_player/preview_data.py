"""Small, transient subset of yt-dlp metadata used for timeline previews."""

import math


def preview_metadata(info: dict) -> dict:
    formats = info.get("formats") or []
    boards = []
    streams = []
    for item in formats:
        if item.get("protocol") == "mhtml":
            if all(item.get(k, 0) > 0 for k in ["width", "height", "rows", "columns", "fps"]):
                boards.append(
                    {
                        key: item.get(key)
                        for key in ["width", "height", "rows", "columns", "fps", "fragments"]
                    }
                )
        elif (
            item.get("vcodec") not in {None, "none"}
            and not item.get("has_drm")
            and str(item.get("url", "")).startswith("https://")
        ):
            headers = {**(info.get("http_headers") or {}), **(item.get("http_headers") or {})}
            streams.append(
                {
                    "url": item["url"],
                    "height": item.get("height") or 10000,
                    "fps": item.get("fps") or 0,
                    "headers": headers,
                    "protocol": item.get("protocol", ""),
                }
            )
    streams.sort(
        key=lambda f: (
            f["protocol"] not in {"http", "https"},
            f["height"] > 360,
            abs(f["height"] - 320),
        )
    )
    boards.sort(key=lambda b: (b["width"] > 320, abs(b["width"] - 320)))
    return {
        "duration": info.get("duration") or 0,
        "storyboard": boards[0] if boards else None,
        "stream": streams[0] if streams else None,
    }


def storyboard_cell(board: dict, seconds: float, duration: float) -> tuple[str, tuple[int, ...]]:
    fps = float(board["fps"])
    if not math.isfinite(fps) or fps <= 0 or duration <= 0:
        raise ValueError("Invalid storyboard timing")
    count = max(1, math.ceil(duration * fps))
    index = min(count - 1, max(0, int(seconds * fps)))
    columns, rows = int(board["columns"]), int(board["rows"])
    sheet, cell = divmod(index, columns * rows)
    width, height = int(board["width"]), int(board["height"])
    url = board["fragments"][sheet]["url"]
    if not url.startswith("https://"):
        raise ValueError("Invalid storyboard URL")
    return url, ((cell % columns) * width, (cell // columns) * height, width, height)
