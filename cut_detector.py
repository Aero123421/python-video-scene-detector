import base64
import inspect
import json
import os
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from string import Template
from typing import Callable, Dict, List

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from scenedetect import SceneManager, open_video
from scenedetect.detectors import AdaptiveDetector, ContentDetector, ThresholdDetector
from pytube import YouTube


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
        except PermissionError:
            if attempt == attempts - 1:
                break
        except OSError:
            if attempt == attempts - 1:
                break

        time.sleep(delay)

    return False


def detect_cuts(path: str, method: str, min_len_frames: int, progress_callback: Callable[[float], None]) -> Dict[str, object]:
    video = open_video(path)
    total_frames = video.duration.get_frames() if video.duration else 0
    fps = float(video.frame_rate) if video.frame_rate else 0.0

    manager = SceneManager()
    if method == "adaptive":
        manager.add_detector(AdaptiveDetector())
    elif method == "content":
        manager.add_detector(ContentDetector())
    else:
        manager.add_detector(ThresholdDetector())

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

    detect_kwargs = {}
    parameters = inspect.signature(manager.detect_scenes).parameters
    if "callback" in parameters:
        detect_kwargs["callback"] = _progress
    elif "callbacks" in parameters:
        detect_kwargs["callbacks"] = [_progress]

    try:
        manager.detect_scenes(video, **detect_kwargs)
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


def _build_segments_html(segments: List[Dict[str, float]], selected_index: int, duration_seconds: float) -> str:
    if not segments:
        return '<div class="timeline-empty">カットは検出されませんでした。</div>'
    parts: List[str] = []
    for idx, seg in enumerate(segments):
        flex_value = seg["duration_seconds"] if duration_seconds else 1.0
        flex_value = max(flex_value, 0.15)
        selected_attr = ' data-selected="true"' if idx == selected_index else ""
        parts.append(
            '<div class="cut-segment"{selected} data-index="{idx}" data-start="{start}" data-end="{end}" '
            'data-duration="{duration}" style="flex:{flex_value};"><span>#{label}</span></div>'.format(
                selected=selected_attr,
                idx=idx,
                start=seg["start_time"],
                end=seg["end_time"],
                duration=seg["duration_seconds"],
                flex_value=flex_value,
                label=seg["index"],
            )
        )
    return "".join(parts)


def render_video_timeline(video_base64: str, mime_type: str, segments: List[Dict[str, float]], selected_index: int, duration_seconds: float) -> None:
    safe_index = selected_index if 0 <= selected_index < len(segments) else 0
    segments_html = _build_segments_html(segments, safe_index, duration_seconds)
    template = Template(
        """
<div class="cut-player">
  <video id="cut-player" controls preload="metadata">
    <source src="data:$mime_type;base64,$video_data" type="$mime_type">
  </video>
  <div class="timeline" id="cut-timeline">
    <div class="timeline-marker" id="cut-marker"></div>
    $segments_html
  </div>
</div>
<script>
(function() {
  const segments = Array.from(document.querySelectorAll(".cut-segment"));
  const selectedIndex = $selected_index;
  const duration = $duration_seconds;
  const player = document.getElementById("cut-player");
  const marker = document.getElementById("cut-marker");

  function markSelection(targetIndex) {
    segments.forEach((segment, index) => {
      if (index === targetIndex) {
        segment.setAttribute("data-selected", "true");
      } else {
        segment.removeAttribute("data-selected");
      }
    });
  }

  function moveMarker(time) {
    if (!marker || !duration) {
      return;
    }
    const bounded = Math.max(0, Math.min(time, duration));
    marker.style.left = (bounded / duration * 100) + "%";
  }

  const initialStart = segments[selectedIndex] ? parseFloat(segments[selectedIndex].dataset.start) : 0;

  function seekToTarget() {
    if (!player || Number.isNaN(initialStart)) {
      return;
    }
    try {
      player.currentTime = initialStart;
    } catch (error) {
      console.warn("seek error", error);
    }
  }

  if (player) {
    if (player.readyState >= 1) {
      seekToTarget();
      moveMarker(player.currentTime);
    } else {
      player.addEventListener("loadedmetadata", () => {
        seekToTarget();
        moveMarker(player.currentTime);
      });
    }
    player.addEventListener("timeupdate", () => moveMarker(player.currentTime));
  }

  segments.forEach((segment, index) => {
    segment.addEventListener("click", () => {
      const start = parseFloat(segment.dataset.start);
      if (!Number.isNaN(start) && player) {
        try {
          player.currentTime = start;
          player.play().catch(() => player.pause());
        } catch (error) {
          console.warn("seek error", error);
        }
      }
      markSelection(index);
      moveMarker(start);
    });
  });

  markSelection(selectedIndex);
})();
</script>
<style>
.cut-player {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.cut-player video {
  width: 100%;
  border-radius: 12px;
  background: #000;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.35);
}
.timeline {
  position: relative;
  display: flex;
  align-items: flex-end;
  gap: 0.35rem;
  height: 52px;
  padding: 0.75rem;
  border-radius: 12px;
  background: linear-gradient(135deg, #f5f7ff 0%, #eef3ff 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
}
.timeline-marker {
  position: absolute;
  top: 6px;
  bottom: 6px;
  width: 2px;
  border-radius: 2px;
  background: #2563eb;
  pointer-events: none;
}
.cut-segment {
  position: relative;
  flex-grow: 1;
  min-width: 6px;
  height: 18px;
  border-radius: 6px;
  background: #60a5fa;
  cursor: pointer;
  transition: transform 0.15s ease, background 0.2s ease, height 0.15s ease, box-shadow 0.2s ease;
}
.cut-segment span {
  position: absolute;
  top: -24px;
  left: 4px;
  font-size: 0.72rem;
  font-weight: 600;
  color: #1f2937;
}
.cut-segment[data-selected="true"] {
  background: #1d4ed8;
  height: 26px;
  box-shadow: 0 6px 12px rgba(37, 99, 235, 0.35);
}
.cut-segment:hover {
  transform: translateY(-4px);
}
.timeline-empty {
  width: 100%;
  text-align: center;
  font-size: 0.9rem;
  color: #475569;
}
</style>
        """
    )
    html_code = template.substitute(
        mime_type=mime_type,
        video_data=video_base64,
        segments_html=segments_html,
        selected_index=safe_index,
        duration_seconds=duration_seconds if duration_seconds else 0,
    )
    components.html(html_code, height=460)


def reset_analysis_state() -> None:
    st.session_state["analysis"] = None
    st.session_state["selected_cut"] = 0
    st.session_state["selected_cut_box"] = 0
    st.session_state["cut_notes"] = {}


def build_output_payload(video_name: str, analysis: Dict[str, object], notes: Dict[str, str]) -> Dict[str, object]:
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


st.set_page_config(page_title="CutOnly - カット解析プレイヤー", layout="wide")

st.title("✂️ CutOnly - カット解析プレイヤー")
st.caption("動画のカット境界をタイムラインで視覚化しながら確認できます。")
st.markdown(
    "1. 動画をアップロードすると左側にプレイヤーが表示されます。\\n"
    "2. 解析ボタンでカットを検出し、右側で各カットの詳細を確認できます。"
)

for key, default_value in {
    "analysis": None,
    "selected_cut": 0,
    "selected_cut_box": 0,
    "video_bytes": b"",
    "video_name": "",
    "video_mime": "video/mp4",
    "source_id": "",
    "video_base64": "",
    "video_base64_id": "",
    "cut_notes": {},
}.items():
    st.session_state.setdefault(key, default_value)

uploaded_file = st.file_uploader(
    "動画ファイルをアップロード", type=["mp4", "mov", "mkv", "avi", "webm"], accept_multiple_files=False
)

youtube_url_input = st.text_input(
    "YouTube の動画リンク",
    value=st.session_state.get("youtube_url_input", ""),
    placeholder="https://www.youtube.com/watch?v=...",
    help="URL を入力するとアップロードの代わりに YouTube から動画を取得します。",
)
st.session_state["youtube_url_input"] = youtube_url_input
youtube_url = youtube_url_input.strip()

if youtube_url:
    expected_source_id = f"youtube:{youtube_url}"
    if st.session_state.get("source_id") != expected_source_id:
        reset_analysis_state()
        try:
            with st.spinner("YouTube から動画をダウンロードしています…"):
                yt = YouTube(youtube_url)
                stream = (
                    yt.streams.filter(progressive=True, file_extension="mp4")
                    .order_by("resolution")
                    .desc()
                    .first()
                )
                if stream is None:
                    raise RuntimeError("MP4 形式のストリームが見つかりませんでした。")
                with tempfile.TemporaryDirectory() as tmpdir:
                    temp_path = Path(stream.download(output_path=tmpdir))
                    video_bytes = temp_path.read_bytes()
                    video_name = temp_path.name
            st.session_state["source_id"] = expected_source_id
            st.session_state["video_bytes"] = video_bytes
            st.session_state["video_name"] = video_name
            st.session_state["video_mime"] = guess_mime_type(video_name)
            st.session_state["video_base64"] = (
                base64.b64encode(video_bytes).decode("utf-8") if video_bytes else ""
            )
            st.session_state["video_base64_id"] = expected_source_id
            st.session_state["cut_notes"] = {}
            st.success("YouTube 動画の取得が完了しました。")
        except Exception as exc:  # pylint: disable=broad-except
            st.session_state["source_id"] = ""
            st.session_state["video_bytes"] = b""
            st.session_state["video_name"] = ""
            st.session_state["video_mime"] = "video/mp4"
            st.session_state["video_base64"] = ""
            st.session_state["video_base64_id"] = ""
            st.error(f"動画のダウンロードに失敗しました: {exc}")

    if uploaded_file is not None:
        st.info("YouTube のリンクが指定されているため、アップロード済みのファイルは無視されます。")
    uploaded_file = None

with st.sidebar:
    st.header("解析設定")
    st.caption("検出アルゴリズムと最小カット長を指定してください。")
    with st.form("analysis_form"):
        method = st.selectbox(
            "検出モード",
            options=("content", "adaptive", "threshold"),
            index=0,
            help="content: 一般的な輝度変化、adaptive: フェードへの感度向上、threshold: 単純な閾値判定",
        )
        min_len = st.number_input(
            "最小カット長 (フレーム数)",
            min_value=1,
            max_value=2000,
            value=15,
            step=1,
            help="このフレーム数より短い区間はカットとして扱いません。",
        )
        submitted = st.form_submit_button(
            "解析を実行",
            use_container_width=True,
            disabled=not bool(st.session_state.get("video_bytes")),
        )

if uploaded_file is not None:
    video_bytes = uploaded_file.getvalue()
    file_id = f"{uploaded_file.name}:{len(video_bytes)}"
    if st.session_state["source_id"] != file_id:
        st.session_state["source_id"] = file_id
        st.session_state["video_bytes"] = video_bytes
        st.session_state["video_name"] = uploaded_file.name
        st.session_state["video_mime"] = uploaded_file.type or guess_mime_type(uploaded_file.name)
        st.session_state["video_base64"] = base64.b64encode(video_bytes).decode("utf-8") if video_bytes else ""
        st.session_state["video_base64_id"] = file_id
        reset_analysis_state()

if submitted:
    video_bytes_state = st.session_state.get("video_bytes", b"")
    video_name_state = st.session_state.get("video_name") or (
        uploaded_file.name if uploaded_file is not None else ""
    )

    if not video_bytes_state:
        st.error("先に動画ファイルをアップロードするか、YouTube リンクを入力してください。")
    else:
        status_placeholder = st.info("解析を開始します…")
        progress_bar = st.progress(0.0)
        temp_path = None

        def update_progress(value: float) -> None:
            clamped = float(min(max(value, 0.0), 1.0))
            progress_bar.progress(clamped)
            status_placeholder.info(f"解析中... {clamped * 100:.1f}%")

        suffix = os.path.splitext(video_name_state)[1] if video_name_state else ""
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix or ".mp4") as tmpfile:
                tmpfile.write(video_bytes_state)
                temp_path = tmpfile.name
            result = detect_cuts(temp_path, method, int(min_len), update_progress)
            progress_bar.progress(1.0)
            status_placeholder.success("解析が完了しました。")
            st.session_state["analysis"] = {
                **result,
                "method": method,
                "min_len_frames": int(min_len),
            }
            st.session_state["selected_cut"] = 0
            st.session_state["selected_cut_box"] = 0
            st.session_state["cut_notes"] = {}
        except Exception as exc:  # pylint: disable=broad-except
            st.session_state["analysis"] = None
            status_placeholder.error(f"解析中にエラーが発生しました: {exc}")
        finally:
            if temp_path and os.path.exists(temp_path):
                if not remove_file_with_retry(temp_path):
                    status_placeholder.warning(
                        "一時ファイルを削除できませんでした。他のアプリケーションでファイルを開いていないか確認してください。"
                    )

analysis = st.session_state.get("analysis")
has_video_source = bool(st.session_state.get("video_bytes"))

if not has_video_source:
    st.info("まずは動画ファイルをアップロードするか、YouTube リンクを入力してください。")
elif analysis is None:
    st.warning("解析を実行すると結果がここに表示されます。")
else:
    segments = analysis.get("segments", [])
    selected_index = st.session_state.get("selected_cut", 0)
    if segments:
        selected_index = max(0, min(selected_index, len(segments) - 1))
    else:
        selected_index = 0
    st.session_state["selected_cut"] = selected_index

    video_col, detail_col = st.columns([2.2, 1.0])
    with video_col:
        if st.session_state["video_base64"]:
            render_video_timeline(
                st.session_state["video_base64"],
                st.session_state["video_mime"],
                segments,
                selected_index,
                float(analysis.get("duration_seconds") or 0.0),
            )
            st.caption("タイムラインのバーをクリックすると、その区間から再生します。右側で詳細を選択すると頭出しします。")
        else:
            st.warning("動画データの取得に失敗しました。")

    with detail_col:
        st.subheader("カット詳細")
        if segments:
            option_indices = list(range(len(segments)))

            def _format_option(idx: int) -> str:
                seg = segments[idx]
                return f"#{seg['index']} {format_seconds(seg['start_time'])} → {format_seconds(seg['end_time'])}"

            selected_index = st.selectbox(
                "対象カット",
                options=option_indices,
                index=selected_index,
                format_func=_format_option,
                key="selected_cut_box",
            )
            st.session_state["selected_cut"] = selected_index
            selected_segment = segments[selected_index]

            st.metric("開始 (フレーム)", f"{selected_segment['start_frame']}")
            st.metric("終了 (フレーム)", f"{selected_segment['end_frame']}")
            st.metric(
                "長さ",
                f"{selected_segment['duration_frames']} fr / {selected_segment['duration_seconds']:.2f} 秒",
            )

            note_state_key = f"note_input_{selected_segment['index']}"
            st.session_state.setdefault("cut_notes", {})
            default_note = st.session_state["cut_notes"].get(f"note_{selected_segment['index']}", "")
            st.session_state.setdefault(note_state_key, default_note)
            note_value = st.text_area(
                "メモ",
                value=default_note,
                key=note_state_key,
                height=120,
                placeholder="気づいた点や編集の狙いを書き留めてください。",
            )
            st.session_state["cut_notes"][f"note_{selected_segment['index']}"] = note_value
        else:
            st.info("カットが検出されませんでした。設定を調整して再度お試しください。")

    summary_col1, summary_col2, summary_col3 = st.columns(3)
    total_cuts = len(segments)
    total_duration = float(analysis.get("duration_seconds") or 0.0)
    fps = float(analysis.get("fps") or 0.0)
    avg_duration_sec = (
        sum(seg["duration_seconds"] for seg in segments) / total_cuts if total_cuts else 0.0
    )
    avg_duration_frames = (
        sum(seg["duration_frames"] for seg in segments) / total_cuts if total_cuts else 0.0
    )

    summary_col1.metric("検出カット数", total_cuts)
    summary_col2.metric("動画尺", format_seconds(total_duration))
    summary_col3.metric(
        "平均カット長",
        f"{avg_duration_frames:.1f} fr / {avg_duration_sec:.2f} 秒",
    )

    st.subheader("カット一覧")
    if segments:
        table_df = pd.DataFrame(
            [
                {
                    "カット": f"#{seg['index']}",
                    "開始フレーム": seg["start_frame"],
                    "終了フレーム": seg["end_frame"],
                    "長さ(フレーム)": seg["duration_frames"],
                    "開始時刻": format_seconds(seg["start_time"]),
                    "終了時刻": format_seconds(seg["end_time"]),
                    "長さ(秒)": f"{seg['duration_seconds']:.2f}",
                }
                for seg in segments
            ]
        )
        st.dataframe(table_df, hide_index=True, use_container_width=True)
    else:
        st.caption("表示できるカット情報がありません。")

    output_payload = build_output_payload(
        st.session_state.get("video_name", uploaded_file.name if uploaded_file else "result"),
        analysis,
        st.session_state.get("cut_notes", {}),
    )
    output_json = json.dumps(output_payload, ensure_ascii=False, indent=2)
    st.download_button(
        "📥 解析結果をJSONで保存",
        data=output_json,
        file_name=f"cuts_{st.session_state.get('video_name', 'result')}.json",
        mime="application/json",
    )
