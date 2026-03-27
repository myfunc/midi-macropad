"""Post-process OBS session segments: stitch, transcribe, burn subtitles."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from logger import get_logger

log = get_logger("obs_session.postprocess")

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_VOICE_SCRIBE_KEY_FILE = Path(__file__).resolve().parents[1] / "voice_scribe" / ".api_key"

STITCHED_NAME = "session_stitched.mp4"
FINAL_NAME = "session_final.mp4"
SUBS_NAME = "session_subtitles.srt"
# Distinct from typical OBS output basenames to avoid concat-list / segment collisions.
CONCAT_LIST_NAME = "_midi_macropad_ffmpeg_concat.txt"
MAX_WHISPER_BYTES = 24 * 1024 * 1024


def load_openai_api_key() -> str:
    """Match Voice Scribe lookup: plugin .api_key, project .env, then environment."""
    if _VOICE_SCRIBE_KEY_FILE.exists():
        key = _VOICE_SCRIBE_KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("OPENAI_API_KEY", "")


def _which_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _run_ffmpeg(args: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=7200,
        )
        err = (proc.stderr or "") + (proc.stdout or "")
        return proc.returncode, err[-4000:]
    except subprocess.TimeoutExpired:
        return -1, "ffmpeg timeout"
    except OSError as exc:
        return -1, str(exc)


def _format_srt_ts(seconds: float) -> str:
    ms_total = int(round(max(0.0, seconds) * 1000.0))
    h, ms_total = divmod(ms_total, 3600_000)
    m, ms_total = divmod(ms_total, 60_000)
    s, ms = divmod(ms_total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _build_concat_list(session_dir: Path, rel_paths: list[str]) -> Path:
    list_path = session_dir / CONCAT_LIST_NAME
    lines = ["ffconcat version 1.0", ""]
    for rp in rel_paths:
        safe = rp.replace("'", r"'\''")
        lines.append(f"file '{safe}'")
        lines.append("")
    list_path.write_text("\n".join(lines), encoding="utf-8")
    return list_path


def _ffmpeg_concat_videos_to_wav(
    session_dir: Path, rel_paths: list[str], wav_out: Path
) -> tuple[bool, str]:
    """Concat segment videos and extract mono 16 kHz WAV (transcript-only multi-segment)."""
    ff = _which_ffmpeg()
    if not ff:
        return False, "ffmpeg not found"
    concat_list = _build_concat_list(session_dir, rel_paths)
    cmd = [
        ff,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_out),
    ]
    code, tail = _run_ffmpeg(cmd, cwd=session_dir)
    if code != 0:
        return False, tail or f"ffmpeg concat wav exit {code}"
    return True, "ok"


def _ffmpeg_concat_stitch(session_dir: Path, concat_list: Path, out_video: Path) -> tuple[bool, str]:
    ff = _which_ffmpeg()
    if not ff:
        return False, "ffmpeg not found"
    cmd = [
        ff,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "fast",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(out_video),
    ]
    code, tail = _run_ffmpeg(cmd, cwd=session_dir)
    if code != 0:
        return False, tail or f"ffmpeg exit {code}"
    return True, "ok"


def _extract_audio_for_whisper(video: Path, wav_path: Path) -> tuple[bool, str]:
    ff = _which_ffmpeg()
    if not ff:
        return False, "ffmpeg not found"
    cmd = [
        ff,
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    code, tail = _run_ffmpeg(cmd)
    if code != 0:
        return False, tail or f"ffmpeg extract exit {code}"
    return True, "ok"


def _split_wav_if_large(
    wav_path: Path, max_bytes: int, work_dir: Path
) -> list[Path]:
    size = wav_path.stat().st_size
    if size <= max_bytes:
        return [wav_path]
    ff = _which_ffmpeg()
    if not ff:
        return [wav_path]
    pattern = str(work_dir / "chunk_%03d.wav")
    cmd = [
        ff,
        "-y",
        "-i",
        str(wav_path),
        "-f",
        "segment",
        "-segment_time",
        "600",
        "-c",
        "copy",
        pattern,
    ]
    code, _ = _run_ffmpeg(cmd)
    if code != 0:
        return [wav_path]
    chunks = sorted(work_dir.glob("chunk_*.wav"))
    return chunks if chunks else [wav_path]


def _transcribe_to_srt(
    wav_paths: list[Path],
    api_key: str,
    model: str,
    language: str | None,
    srt_out: Path,
) -> tuple[bool, str]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    srt_blocks: list[str] = []
    cue = 1
    offset = 0.0
    lang_kw = {}
    if language:
        lang_kw["language"] = language

    for wav in wav_paths:
        try:
            with open(wav, "rb") as audio_f:
                audio_f.name = wav.name
                resp = client.audio.transcriptions.create(
                    model=model,
                    file=audio_f,
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                    **lang_kw,
                )
        except Exception as exc:
            return False, f"whisper failed: {exc}"

        segments = getattr(resp, "segments", None) or []
        for seg in segments:
            start = float(getattr(seg, "start", 0.0)) + offset
            end = float(getattr(seg, "end", 0.0)) + offset
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            text = re.sub(r"\s+", " ", text)
            srt_blocks.append(
                f"{cue}\n{_format_srt_ts(start)} --> {_format_srt_ts(end)}\n{text}\n"
            )
            cue += 1

        dur = float(getattr(resp, "duration", 0.0) or 0.0)
        offset += dur

    if not srt_blocks:
        return False, "no segments from transcription"

    srt_out.write_text("\n".join(srt_blocks) + "\n", encoding="utf-8")
    return True, "ok"


def _burn_subtitles(video_in: Path, srt_path: Path, video_out: Path) -> tuple[bool, str]:
    ff = _which_ffmpeg()
    if not ff:
        return False, "ffmpeg not found"
    srt_esc = str(srt_path.resolve()).replace("\\", "/").replace(":", r"\:")
    vf = (
        f"subtitles='{srt_esc}':force_style="
        r"'Fontsize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=1'"
      )
    cmd = [
        ff,
        "-y",
        "-i",
        str(video_in),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-preset",
        "fast",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(video_out),
    ]
    code, tail = _run_ffmpeg(cmd)
    if code != 0:
        return False, tail or f"ffmpeg burn exit {code}"
    return True, "ok"


def reveal_session_folder(path: Path) -> None:
    """Open *path* (file or directory) in the OS file manager."""
    _reveal_folder_impl(path)


def _reveal_folder_impl(path: Path) -> None:
    try:
        p = path if path.is_dir() else path.parent
        if sys.platform == "win32":
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
    except Exception as exc:
        log.warning("reveal folder: %s", exc)


@dataclass
class PostProcessResult:
    stitch: dict
    transcript: dict
    stitched_path: str | None
    final_path: str | None
    srt_path: str | None


def run_session_postprocess(
    session_folder: Path,
    *,
    do_stitch: bool,
    do_transcript: bool,
    do_open_folder: bool,
    transcription_model: str = "whisper-1",
    transcript_language: str | None = None,
) -> PostProcessResult:
    """Read ``session_manifest.json`` in *session_folder* and run enabled steps."""
    manifest_path = session_folder / "session_manifest.json"
    stitched_path: Path | None = None
    final_path: Path | None = None
    srt_path: Path | None = None

    stitch_info: dict = {
        "status": "skipped",
        "message": "stitching disabled" if not do_stitch else "",
    }
    trans_info: dict = {
        "status": "skipped",
        "message": "transcript disabled" if not do_transcript else "",
    }

    if not manifest_path.exists():
        stitch_info = {"status": "error", "message": "session_manifest.json missing"}
        return PostProcessResult(stitch_info, trans_info, None, None, None)

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        stitch_info = {"status": "error", "message": f"manifest read: {exc}"}
        return PostProcessResult(stitch_info, trans_info, None, None, None)

    segments = manifest.get("segments") or []
    media_rels: list[str] = []
    seen_media: set[str] = set()
    for seg in sorted(segments, key=lambda s: int(s.get("index", 0))):
        rel = seg.get("session_file_path") or seg.get("file_path")
        if not rel:
            continue
        p = Path(rel)
        if not p.is_absolute():
            p = session_folder / p.name
        if p.exists() and p.suffix.lower() in (
            ".mp4",
            ".mkv",
            ".mov",
            ".webm",
            ".avi",
        ):
            key = str(p.resolve())
            if key in seen_media:
                continue
            seen_media.add(key)
            media_rels.append(p.name)

    if do_stitch and not media_rels:
        stitch_info = {
            "status": "error",
            "message": "no segment video files found to stitch",
        }
    elif do_stitch:
        concat_list = _build_concat_list(session_folder, media_rels)
        out_v = session_folder / STITCHED_NAME
        ok, msg = _ffmpeg_concat_stitch(session_folder, concat_list, out_v)
        if ok:
            stitch_info = {"status": "ok", "message": STITCHED_NAME}
            stitched_path = out_v
        else:
            stitch_info = {"status": "error", "message": msg[:1200]}

    video_for_subs = stitched_path if stitched_path and stitched_path.exists() else None
    if not video_for_subs and media_rels:
        if len(media_rels) > 1 and do_transcript and not do_stitch:
            video_for_subs = None
        else:
            candidate = session_folder / media_rels[0]
            if candidate.exists():
                video_for_subs = candidate

    if do_transcript:
        api_key = load_openai_api_key()
        concat_transcript = (
            not video_for_subs and len(media_rels) > 1 and not do_stitch
        )
        if not api_key.strip():
            trans_info = {"status": "error", "message": "OpenAI API key not configured"}
        elif not media_rels:
            trans_info = {
                "status": "error",
                "message": "no segment video files for transcription",
            }
        elif not video_for_subs and not concat_transcript:
            trans_info = {
                "status": "error",
                "message": "no stitched or segment video for transcription",
            }
        elif not _which_ffmpeg():
            trans_info = {"status": "error", "message": "ffmpeg not found"}
        else:
            with tempfile.TemporaryDirectory(dir=str(session_folder)) as tmp:
                tmp_p = Path(tmp)
                wav = tmp_p / "full.wav"
                media_src = video_for_subs
                if concat_transcript:
                    merged_seg = tmp_p / "segments_concat.wav"
                    ok_c, c_msg = _ffmpeg_concat_videos_to_wav(
                        session_folder, media_rels, merged_seg
                    )
                    if not ok_c or not merged_seg.exists():
                        trans_info = {
                            "status": "error",
                            "message": (c_msg or "segment concat failed")[:1200],
                        }
                    else:
                        media_src = merged_seg
                if trans_info.get("status") != "error" and media_src is not None:
                    ok_ex, ex_msg = _extract_audio_for_whisper(media_src, wav)
                    if not ok_ex:
                        trans_info = {"status": "error", "message": ex_msg[:1200]}
                    else:
                        chunks = _split_wav_if_large(wav, MAX_WHISPER_BYTES, tmp_p)
                        srt_file = session_folder / SUBS_NAME
                        ok_tr, tr_msg = _transcribe_to_srt(
                            chunks,
                            api_key,
                            transcription_model,
                            transcript_language,
                            srt_file,
                        )
                        if ok_tr:
                            msg = SUBS_NAME
                            if concat_transcript:
                                msg = f"{SUBS_NAME} ({len(media_rels)} segments)"
                            trans_info = {"status": "ok", "message": msg}
                            srt_path = srt_file
                        else:
                            trans_info = {
                                "status": "error",
                                "message": tr_msg[:1200],
                            }

    burn_video = video_for_subs
    if (
        do_transcript
        and srt_path
        and srt_path.exists()
        and not stitched_path
        and not burn_video
        and len(media_rels) > 1
        and not do_stitch
    ):
        aux_v = session_folder / "_session_concat_for_subs.mp4"
        cl = _build_concat_list(session_folder, media_rels)
        ok_bv, msg_bv = _ffmpeg_concat_stitch(session_folder, cl, aux_v)
        if ok_bv and aux_v.exists():
            burn_video = aux_v

    if srt_path and srt_path.exists() and stitched_path and stitched_path.exists():
        final_p = session_folder / FINAL_NAME
        ok_burn, burn_msg = _burn_subtitles(stitched_path, srt_path, final_p)
        if ok_burn:
            final_path = final_p
            trans_info["burned"] = FINAL_NAME
        else:
            trans_info["burn_error"] = burn_msg[:800]
    elif srt_path and srt_path.exists() and burn_video and not stitched_path:
        final_p = session_folder / FINAL_NAME
        ok_burn, burn_msg = _burn_subtitles(burn_video, srt_path, final_p)
        if ok_burn:
            final_path = final_p
            trans_info["burned"] = FINAL_NAME
        else:
            trans_info["burn_error"] = burn_msg[:800]

    manifest.setdefault("postprocess", {})
    manifest["postprocess"]["stitch"] = stitch_info
    manifest["postprocess"]["transcript"] = trans_info
    manifest["postprocess"]["artifacts"] = {
        "stitched": str(stitched_path) if stitched_path else None,
        "final": str(final_path) if final_path else None,
        "subtitles": str(srt_path) if srt_path else None,
    }
    try:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except OSError as exc:
        log.error("manifest update failed: %s", exc)

    if do_open_folder:
        target = final_path or stitched_path or session_folder
        if target:
            _reveal_folder_impl(target)

    return PostProcessResult(
        stitch_info,
        trans_info,
        str(stitched_path) if stitched_path else None,
        str(final_path) if final_path else None,
        str(srt_path) if srt_path else None,
    )
