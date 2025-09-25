import base64
import inspect
import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Callable, Dict, List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from scenedetect import SceneManager, StatsManager, open_video
from scenedetect.detectors import AdaptiveDetector, ContentDetector, ThresholdDetector


DEFAULT_METHOD = "content"
SUPPORTED_METHODS = {"content", "adaptive", "threshold"}
DEFAULT_MIN_LEN = 15
MIN_MIN_LEN = 1
MAX_MIN_LEN = 2000

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"

app = FastAPI(title="CutOnly Analyzer")
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))


def format_seconds(value: float) -> str:
    if value is None or value < 0:
        return "-"
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes:02d}:{seconds:05.2f}"


def guess_mime_type(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
        ".webm": "video/webm",
    }.get(ext, "video/mp4")


def remove_file_with_retry(path: str, attempts: int = 5, delay: float = 0.2) -> bool:
    if not path:
        return True

    file_path = Path(path)
    for attempt in range(attempts):
        try:
            file_path.unlink()
            return True
        except FileNotFoundError:
            return True
        except (PermissionError, OSError):
            if attempt == attempts - 1:
                break
        time.sleep(delay)

    return False


def detect_cuts(path: str, method: str, min_len_frames: int, progress_callback: Callable[[float], None]) -> Dict[str, object]:
    video = open_video(path)
    total_frames = video.duration.get_frames() if video.duration else 0
    fps = float(video.frame_rate) if video.frame_rate else 0.0

    stats_manager = StatsManager()
    manager = SceneManager(stats_manager)
    min_scene_len = max(1, int(min_len_frames))
    if method == "adaptive":
        manager.add_detector(AdaptiveDetector(min_scene_len=min_scene_len))
    elif method == "content":
        manager.add_detector(ContentDetector(min_scene_len=min_scene_len))
    else:
        manager.add_detector(ThresholdDetector(min_scene_len=min_scene_len, add_final_scene=True))

    def _progress(*args, **kwargs):
        if not total_frames:
            return

        frame_time = None
        if args:
            frame_time = args[0]
        elif "frame_time" in kwargs:
            frame_time = kwargs["frame_time"]

        if frame_time is None:
            return

        try:
            frame_idx = frame_time.get_frames()  # type: ignore[attr-defined]
        except AttributeError:
            try:
                frame_idx = int(frame_time)
            except (TypeError, ValueError):
                return

        fraction = min(max(frame_idx, 0) / total_frames, 0.999)
        with suppress(Exception):
            progress_callback(fraction)

    detect_kwargs: Dict[str, object] = {}
    parameters = inspect.signature(manager.detect_scenes).parameters
    if "callback" in parameters:
        detect_kwargs["callback"] = _progress
    elif "callbacks" in parameters:
        detect_kwargs["callbacks"] = [_progress]

    try:
        manager.detect_scenes(video, **detect_kwargs)
    except IndexError as exc:
        raise RuntimeError("フレーム処理中に内部エラーが発生しました。閾値や最小カット長を調整して再試行してください。") from exc
    finally:
        release = getattr(video, "release", None)
        if callable(release):
            with suppress(Exception):
                release()
        close = getattr(video, "close", None)
        if callable(close):
            with suppress(Exception):
                close()

    scenes = manager.get_scene_list()
    segments: List[Dict[str, float]] = []
    for start_timecode, end_timecode in scenes:
        start_frame = start_timecode.get_frames()
        end_frame = end_timecode.get_frames()
        duration_frames = end_frame - start_frame
        if duration_frames < min_len_frames:
            continue
        start_seconds = start_frame / fps if fps else 0.0
        end_seconds = end_frame / fps if fps else 0.0
        segments.append(
            {
                "index": len(segments) + 1,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "duration_frames": duration_frames,
                "start_time": start_seconds,
                "end_time": end_seconds,
                "duration_seconds": max(end_seconds - start_seconds, 0.0),
            }
        )

    duration_seconds = total_frames / fps if fps else 0.0

    return {
        "segments": segments,
        "total_frames": total_frames,
        "fps": fps,
        "duration_seconds": duration_seconds,
    }


def build_output_payload(video_name: str, analysis: Dict[str, object], notes: Optional[Dict[str, str]] = None) -> Dict[str, object]:
    notes = notes or {}
    cuts_payload: List[Dict[str, object]] = []
    for seg in analysis.get("segments", []):
        note_key = f"note_{seg['index']}"
        item = {
            "index": seg["index"],
            "start_frame": seg["start_frame"],
            "end_frame": seg["end_frame"],
            "duration_frames": seg["duration_frames"],
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "duration_seconds": seg["duration_seconds"],
        }
        note_value = notes.get(note_key, "").strip()
        if note_value:
            item["note"] = note_value
        cuts_payload.append(item)
    return {
        "input": video_name,
        "method": analysis.get("method"),
        "min_len_frames": analysis.get("min_len_frames"),
        "fps": analysis.get("fps"),
        "total_frames": analysis.get("total_frames"),
        "duration_seconds": analysis.get("duration_seconds"),
        "cuts": cuts_payload,
    }


def clamp_min_len(value: int) -> int:
    return max(MIN_MIN_LEN, min(MAX_MIN_LEN, int(value)))


def prepare_segments_for_ui(
    raw_segments: List[Dict[str, float]],
    longest_duration: float,
) -> List[Dict[str, object]]:
    max_duration = float(longest_duration or 0.0)
    if max_duration <= 0:
        max_duration = max(
            (float(seg.get("duration_seconds", 0.0) or 0.0) for seg in raw_segments),
            default=0.0,
        )
    prepared: List[Dict[str, object]] = []
    for seg in raw_segments:
        duration_seconds = float(seg.get("duration_seconds", 0.0) or 0.0)
        ratio = duration_seconds / max_duration if max_duration else 0.0
        prepared.append(
            {
                **seg,
                "start_label": format_seconds(seg.get("start_time", 0.0)),
                "end_label": format_seconds(seg.get("end_time", 0.0)),
                "duration_label": f"{seg['duration_frames']} fr / {duration_seconds:.2f} 秒",
                "duration_brief": f"{duration_seconds:.2f} 秒",
                "duration_ratio": max(0.0, min(ratio, 1.0)),
            }
        )
    return prepared


def build_default_context(request: Request) -> Dict[str, object]:
    return {
        "request": request,
        "form": {"method": DEFAULT_METHOD, "min_len": DEFAULT_MIN_LEN},
        "message": None,
        "error": None,
        "result": None,
        "MIN_MIN_LEN": MIN_MIN_LEN,
        "MAX_MIN_LEN": MAX_MIN_LEN,
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", build_default_context(request))


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(
    request: Request,
    video_file: UploadFile = File(None),
    method: str = Form(DEFAULT_METHOD),
    min_len: int = Form(DEFAULT_MIN_LEN),
) -> HTMLResponse:
    clamped_min_len = clamp_min_len(min_len)
    form_state = {
        "method": method,
        "min_len": clamped_min_len,
    }

    message = None
    error = None
    result_payload: Optional[Dict[str, object]] = None

    method = method if method in SUPPORTED_METHODS else DEFAULT_METHOD
    min_len_frames = form_state["min_len"]

    temp_paths: List[str] = []
    video_bytes: Optional[bytes] = None
    video_name = ""
    video_mime = "video/mp4"
    detection_path: Optional[str] = None

    if video_file and video_file.filename:
        video_bytes = await video_file.read()
        if not video_bytes:
            error = "アップロードされたファイルが空のようです。"
        else:
            suffix = Path(video_file.filename).suffix or ".mp4"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmpfile:
                tmpfile.write(video_bytes)
                detection_path = tmpfile.name
            temp_paths.append(detection_path)
            video_name = video_file.filename
            video_mime = video_file.content_type or guess_mime_type(video_file.filename)
    else:
        error = "動画ファイルをアップロードしてください。"
    analysis_result: Optional[Dict[str, object]] = None
    elapsed_ms = 0.0

    if not error and detection_path:
        if video_bytes is None:
            try:
                video_bytes = Path(detection_path).read_bytes()
            except OSError as exc:
                error = f"動画データの読み込みに失敗しました: {exc}"

    if not error and detection_path and video_bytes:
        started_at = time.time()
        try:
            analysis_result = detect_cuts(
                detection_path,
                method,
                int(min_len_frames),
                lambda _: None,
            )
        except Exception as exc:  # pylint: disable=broad-except
            error = f"解析中にエラーが発生しました: {exc}"
        else:
            elapsed_ms = (time.time() - started_at) * 1000.0
            analysis_result.update({
                "method": method,
                "min_len_frames": int(min_len_frames),
            })

    for path in temp_paths:
        remove_file_with_retry(path)

    if not error and analysis_result and video_bytes:
        raw_segments: List[Dict[str, float]] = analysis_result.get("segments", [])  # type: ignore[assignment]
        longest_duration = max((float(seg.get("duration_seconds", 0.0) or 0.0) for seg in raw_segments), default=0.0)
        segments = prepare_segments_for_ui(raw_segments, longest_duration)
        selected_index = 0 if segments else -1

        video_data_url = f"data:{video_mime};base64,{base64.b64encode(video_bytes).decode('ascii')}"
        total_cuts = len(segments)
        total_duration_seconds = float(analysis_result.get("duration_seconds") or 0.0)
        total_duration_label = format_seconds(total_duration_seconds)
        fps = float(analysis_result.get("fps") or 0.0)
        avg_duration_sec = (
            sum(seg["duration_seconds"] for seg in raw_segments) / total_cuts if total_cuts else 0.0
        )
        avg_duration_frames = (
            sum(seg["duration_frames"] for seg in raw_segments) / total_cuts if total_cuts else 0.0
        )

        output_payload = build_output_payload(video_name or "result", analysis_result)
        output_json = json.dumps(output_payload, ensure_ascii=False, indent=2)
        json_data_url = f"data:application/json;base64,{base64.b64encode(output_json.encode('utf-8')).decode('ascii')}"

        result_payload = {
            "video_name": video_name or "動画",
            "video_data_url": video_data_url,
            "segments": segments,
            "segments_json": json.dumps(segments, ensure_ascii=False),
            "selected_index": selected_index,
            "total_cuts": total_cuts,
            "total_duration_label": total_duration_label,
            "total_duration_seconds": total_duration_seconds,
            "longest_segment_seconds": longest_duration,
            "fps": fps,
            "avg_duration_frames": avg_duration_frames,
            "avg_duration_label": format_seconds(avg_duration_sec),
            "avg_duration_compact": f"{avg_duration_frames:.1f} fr / {avg_duration_sec:.2f} 秒",
            "download_href": json_data_url,
            "analysis_json": output_json,
            "elapsed_ms": elapsed_ms,
        }
        message = "検出が完了しました。"
    elif not error:
        error = "解析結果が得られませんでした。設定を調整して再度お試しください。"

    context = {
        "request": request,
        "form": form_state,
        "message": message,
        "error": error,
        "result": result_payload,
        "MIN_MIN_LEN": MIN_MIN_LEN,
        "MAX_MIN_LEN": MAX_MIN_LEN,
    }
    return templates.TemplateResponse("index.html", context)

