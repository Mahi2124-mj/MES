import csv
import os
import time
from typing import Dict, Optional, Tuple

import cv2


DEFAULT_METADATA_CSV = "cycles.csv"


def _resolve_csv_path(metadata_csv: str, base_dir: Optional[str]) -> str:
    if os.path.isabs(metadata_csv):
        return metadata_csv
    root = base_dir or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(root, metadata_csv)


def _load_cycle_map(csv_path: str) -> Dict[int, Dict[str, str]]:
    cycle_map: Dict[int, Dict[str, str]] = {}
    if not os.path.exists(csv_path):
        return cycle_map

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cycle_number = int(row["cycle_number"])
            except (TypeError, ValueError, KeyError):
                continue
            cycle_map[cycle_number] = row
    return cycle_map


def resolve_cycle_video_path(
    cycle_number: int,
    metadata_csv: str = DEFAULT_METADATA_CSV,
    base_dir: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    csv_path = _resolve_csv_path(metadata_csv, base_dir)
    cycle_map = _load_cycle_map(csv_path)

    if cycle_number not in cycle_map:
        return None, f"Cycle {cycle_number} not found in metadata"

    raw_path = cycle_map[cycle_number].get("file_path", "")
    if not raw_path:
        return None, f"Cycle {cycle_number} has no video path in metadata"

    if os.path.isabs(raw_path):
        video_path = raw_path
    else:
        root = base_dir or os.path.dirname(os.path.abspath(__file__))
        video_path = os.path.join(root, raw_path)

    if not os.path.exists(video_path):
        return None, f"Video file does not exist: {video_path}"

    return video_path, None


def play_cycle(
    cycle_number: int,
    metadata_csv: str = DEFAULT_METADATA_CSV,
    base_dir: Optional[str] = None,
) -> Tuple[bool, str]:
    video_path, error = resolve_cycle_video_path(cycle_number, metadata_csv, base_dir)
    if error:
        return False, error

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return False, f"Unable to open video: {video_path}"

    window_name = f"Cycle {cycle_number} Playback"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    fps = cap.get(cv2.CAP_PROP_FPS)
    delay_ms = int(1000 / fps) if fps and fps > 0 else 33

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        cv2.putText(
            frame,
            f"Playing Cycle {cycle_number} (press q to close)",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
        )
        cv2.imshow(window_name, frame)

        key = cv2.waitKey(delay_ms) & 0xFF
        if key == ord("q"):
            break

    cap.release()
    cv2.destroyWindow(window_name)
    time.sleep(0.05)
    return True, f"Played cycle {cycle_number}"


if __name__ == "__main__":
    # Quick manual test
    success, message = play_cycle(1)
    print(message)
