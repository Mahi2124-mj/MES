import argparse
import csv
import os
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

import cv2

from camera_config import get_active_rtsp_url, get_camera_rtsp_url
from camera_discovery import get_onvif_rtsp_uris


# Last-resort RTSP URL — used only if cameras.json is missing AND env
# isn't set.  Real installs come from the active camera config; this
# placeholder is empty so a misconfig fails loudly instead of recording
# from some random hardcoded camera.
import os
DEFAULT_RTSP_URL = os.getenv("DEFAULT_RTSP_URL", "")
DEFAULT_METADATA_CSV = "cycles.csv"
# DEFAULT_VIDEOS_DIR is the *legacy* fallback name for relative-path mode
# (videos under backend/videos/).  The actual resolved path now goes
# through settings_config.get_videos_dir() which honors:
#   env VIDEOS_DIR > settings.json > this default
DEFAULT_VIDEOS_DIR = "videos"


def get_resolved_videos_dir() -> str:
    """Public helper for callers that want the currently-active videos
    directory (absolute path).  Honours env override and settings.json."""
    try:
        from settings_config import get_videos_dir
        return get_videos_dir()
    except Exception:
        # Fallback: resolve relative to this file's folder
        base = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base, DEFAULT_VIDEOS_DIR)

NEW_CSV_COLUMNS = [
    "cycle_number", "start_time", "end_time", "duration", "file_path",
    "machine_id", "machine_name", "line_name", "zone_name", "shift", "tag",
]


def _get_shift(dt: datetime) -> str:
    h = dt.hour
    if 6 <= h < 14:
        return "Morning"
    elif 14 <= h < 22:
        return "Evening"
    return "Night"


def _mask_rtsp_url(url: str) -> str:
    """Return URL with password replaced by *** for safe logging."""
    try:
        p = urlparse(url)
        if p.password:
            netloc = f"{p.username}:***@{p.hostname}:{p.port or 554}"
            return urlunparse((p.scheme, netloc, p.path, p.params, p.query, p.fragment))
    except Exception:
        pass
    return url


def build_rtsp_candidates(rtsp_url: str) -> list:
    parsed = urlparse(rtsp_url)
    if not parsed.scheme.startswith("rtsp") or not parsed.hostname:
        return [rtsp_url]

    username = parsed.username or "admin"
    password = parsed.password or "admin123"
    host = parsed.hostname
    port = parsed.port or 554
    auth_base = f"rtsp://{username}:{password}@{host}:{port}"

    candidates = [
        rtsp_url,
        f"{auth_base}/h264/ch1/main/av_stream",
        f"{auth_base}/h264/ch1/sub/av_stream",
        f"{auth_base}/MediaInput/h264/stream_1",
        f"{auth_base}/MediaInput/h264",
        f"{auth_base}/live/ch00_0",
    ]

    deduped: list = []
    for c in candidates:
        if c not in deduped:
            deduped.append(c)
    return deduped


def open_rtsp_capture(rtsp_url: str):
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

    parsed = urlparse(rtsp_url)
    tried_urls: list = []
    for candidate in build_rtsp_candidates(rtsp_url):
        tried_urls.append(candidate)
        print(f"Trying camera stream: {_mask_rtsp_url(candidate)}")
        cap = cv2.VideoCapture(candidate, cv2.CAP_FFMPEG)
        if cap.isOpened():
            ok, _frame = cap.read()
            if ok:
                print(f"Connected using: {_mask_rtsp_url(candidate)}")
                return cap, candidate, tried_urls
        cap.release()

    if parsed.hostname:
        onvif_candidates = get_onvif_rtsp_uris(
            parsed.hostname,
            parsed.username or "admin",
            parsed.password or "admin123",
        )
        for candidate in onvif_candidates:
            if candidate in tried_urls:
                continue
            tried_urls.append(candidate)
            print(f"Trying ONVIF stream: {_mask_rtsp_url(candidate)}")
            cap = cv2.VideoCapture(candidate, cv2.CAP_FFMPEG)
            if cap.isOpened():
                ok, _frame = cap.read()
                if ok:
                    print(f"Connected using ONVIF stream: {_mask_rtsp_url(candidate)}")
                    return cap, candidate, tried_urls
            cap.release()

    return None, None, tried_urls


def ensure_metadata_file(csv_path: str) -> None:
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(NEW_CSV_COLUMNS)
        return
    # Upgrade existing files that have fewer columns than the new schema
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            header = []
    missing_cols = [c for c in NEW_CSV_COLUMNS if c not in header]
    if missing_cols:
        rows = []
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for col in missing_cols:
                    row.setdefault(col, "")
                rows.append({c: row.get(c, "") for c in NEW_CSV_COLUMNS})
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=NEW_CSV_COLUMNS)
            w.writeheader()
            w.writerows(rows)


def get_next_cycle_number(csv_path: str) -> int:
    if not os.path.exists(csv_path):
        return 1
    max_cycle = 0
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cycle = int(row.get("cycle_number", 0))
                max_cycle = max(max_cycle, cycle)
            except (TypeError, ValueError):
                continue
    return max_cycle + 1 if max_cycle > 0 else 1


def append_cycle_metadata(
    csv_path: str,
    cycle_number: int,
    start_dt: datetime,
    end_dt: datetime,
    relative_file_path: str,
    machine_id: str = "",
    machine_name: str = "",
    line_name: str = "",
    zone_name: str = "",
    tag: str = "",
) -> None:
    duration_seconds = max(0, int((end_dt - start_dt).total_seconds()))
    shift = _get_shift(start_dt)
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            cycle_number,
            start_dt.strftime("%Y-%m-%dT%H:%M:%S"),   # full ISO datetime
            end_dt.strftime("%Y-%m-%dT%H:%M:%S"),     # full ISO datetime
            duration_seconds,
            relative_file_path,
            machine_id,
            machine_name,
            line_name,
            zone_name,
            shift,
            tag,
        ])


def update_cycle_tag(csv_path: str, cycle_number: int, tag: str) -> Tuple[bool, str]:
    if not os.path.exists(csv_path):
        return False, "CSV not found"
    rows = []
    found = False
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or NEW_CSV_COLUMNS)
        for row in reader:
            try:
                cn = int(row.get("cycle_number", 0))
            except (TypeError, ValueError):
                cn = 0
            if cn == cycle_number:
                row["tag"] = tag
                found = True
            rows.append(row)
    if not found:
        return False, "Cycle not found"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return True, f"Tag '{tag}' applied to cycle {cycle_number}"


def create_video_writer(file_abs: str, fps: float, frame_size: tuple):
    codec_candidates = ["avc1", "H264", "mp4v"]
    for codec in codec_candidates:
        writer = cv2.VideoWriter(file_abs, cv2.VideoWriter_fourcc(*codec), fps, frame_size)
        if writer.isOpened():
            print(f"Recording codec: {codec}")
            return writer
        writer.release()
    raise RuntimeError("Unable to create video writer for MP4 output")


class CycleRecorder:
    def __init__(self, rtsp_url: str, metadata_csv: str, videos_dir: str):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.rtsp_url = rtsp_url
        self.metadata_csv = (
            metadata_csv if os.path.isabs(metadata_csv)
            else os.path.join(self.base_dir, metadata_csv)
        )
        # videos_dir resolution: argparse default ("videos") triggers the
        # settings/env override path; an explicit user-supplied value wins.
        if videos_dir == DEFAULT_VIDEOS_DIR:
            self.videos_dir = get_resolved_videos_dir()
        elif os.path.isabs(videos_dir):
            self.videos_dir = videos_dir
        else:
            self.videos_dir = os.path.join(self.base_dir, videos_dir)
        os.makedirs(self.videos_dir, exist_ok=True)
        ensure_metadata_file(self.metadata_csv)

        self.cap, self.connected_url, self.tried_urls = open_rtsp_capture(self.rtsp_url)
        self.writer = None
        self.current_cycle = None
        self.current_cycle_file_rel = None
        self.current_start_dt = None
        self.next_cycle_number = get_next_cycle_number(self.metadata_csv)

    def start_cycle(self, frame_shape, fps: float):
        if self.writer is not None:
            return
        cycle_number = self.next_cycle_number
        self.next_cycle_number += 1
        file_name = f"cycle_{cycle_number}.mp4"
        file_abs = os.path.join(self.videos_dir, file_name)
        # When the videos folder lives INSIDE the backend dir we keep
        # the legacy "videos/cycle_N.mp4" relative path in the CSV so
        # api_server.serve_video can resolve it against BASE_DIR.  When
        # it lives OUTSIDE the backend (e.g. F:\\CameraCMS_Videos for an
        # external HDD) we MUST store the absolute path — otherwise
        # serve_video joins it with BASE_DIR and 404s.
        try:
            common = os.path.commonpath([os.path.abspath(self.videos_dir), self.base_dir])
        except ValueError:
            common = ""   # different drives → certainly outside
        if common == self.base_dir:
            rel_dir = os.path.relpath(self.videos_dir, self.base_dir).replace("\\", "/")
            file_rel = f"{rel_dir}/{file_name}"
        else:
            file_rel = file_abs.replace("\\", "/")   # absolute path
        height, width = frame_shape[:2]
        safe_fps = fps if fps and fps > 0 else 25.0
        self.writer = create_video_writer(file_abs, safe_fps, (width, height))
        self.current_cycle = cycle_number
        self.current_cycle_file_rel = file_rel
        self.current_start_dt = datetime.now()
        print(f"Started cycle {cycle_number}: {file_rel}")

    def end_cycle(self):
        if self.writer is None:
            return
        end_dt = datetime.now()
        self.writer.release()
        self.writer = None
        append_cycle_metadata(
            self.metadata_csv,
            self.current_cycle,
            self.current_start_dt,
            end_dt,
            self.current_cycle_file_rel,
        )
        print(
            f"Ended cycle {self.current_cycle}: "
            f"{self.current_start_dt.strftime('%H:%M:%S')} -> {end_dt.strftime('%H:%M:%S')}"
        )
        self.current_cycle = None
        self.current_cycle_file_rel = None
        self.current_start_dt = None

    def run(self):
        if self.cap is None or not self.cap.isOpened():
            tried = "\n".join(f"- {url}" for url in self.tried_urls)
            raise RuntimeError(
                "Unable to open camera stream. Tried these RTSP URLs:\n"
                f"{tried}\n"
                "Check camera RTSP settings, stream path, and ONVIF enablement."
            )
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        cv2.namedWindow("Live Camera", cv2.WINDOW_NORMAL)
        while True:
            ok, frame = self.cap.read()
            if not ok:
                print("Frame read failed. Retrying...")
                if cv2.waitKey(250) & 0xFF == ord("q"):
                    break
                continue
            if self.writer is not None:
                self.writer.write(frame)
            status = (
                f"Recording cycle {self.current_cycle}"
                if self.writer is not None else "Idle"
            )
            cv2.putText(frame, "Keys: s=start, e=end, q=quit",
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.putText(frame, status,
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                        (0, 255, 0) if self.writer is not None else (255, 255, 255), 2)
            cv2.imshow("Live Camera", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("s"):
                self.start_cycle(frame.shape, fps)
            elif key == ord("e"):
                self.end_cycle()
            elif key == ord("q"):
                break
        if self.writer is not None:
            self.end_cycle()
        self.cap.release()
        cv2.destroyAllWindows()


def parse_args():
    parser = argparse.ArgumentParser(description="RTSP cycle recorder")
    parser.add_argument("--rtsp", default=None, help="RTSP camera URL (overrides camera config)")
    parser.add_argument("--camera-id", default=None, help="Camera id from cameras.json")
    parser.add_argument("--csv", default=DEFAULT_METADATA_CSV, help="Metadata CSV path")
    parser.add_argument("--videos", default=DEFAULT_VIDEOS_DIR, help="Directory to save videos")
    return parser.parse_args()


def resolve_rtsp_url(rtsp_arg, camera_id, base_dir: str) -> str:
    if rtsp_arg:
        return rtsp_arg
    if camera_id:
        camera_rtsp = get_camera_rtsp_url(camera_id, base_dir)
        if camera_rtsp:
            return camera_rtsp
        print(f"Camera id not found: {camera_id}. Falling back to active/default camera.")
    try:
        return get_active_rtsp_url(base_dir)
    except Exception:
        return DEFAULT_RTSP_URL


def main():
    args = parse_args()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    rtsp_url = resolve_rtsp_url(args.rtsp, args.camera_id, base_dir)
    print(f"Using RTSP URL: {rtsp_url}")
    recorder = CycleRecorder(rtsp_url, args.csv, args.videos)
    recorder.run()


if __name__ == "__main__":
    main()
