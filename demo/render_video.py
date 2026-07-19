from __future__ import annotations

import argparse
import json
import math
import os
from bisect import bisect_right
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

if __package__:
    from .video_evidence import (
        find_unique_action_step,
        frame_second,
        load_json,
        sha256,
        validate_inputs,
    )
else:
    from video_evidence import (
        find_unique_action_step,
        frame_second,
        load_json,
        sha256,
        validate_inputs,
    )

try:
    import imageio_ffmpeg
except ImportError as error:  # pragma: no cover - exercised by the CLI environment
    raise SystemExit(
        "Video dependencies are missing. Run: "
        "python -m pip install -r demo/requirements-video.txt"
    ) from error


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = REPO_ROOT / "demo" / "scenarios" / "rainstorm-p5-120s.json"
DEFAULT_EVIDENCE = REPO_ROOT / "demo" / "artifacts" / "latest-evidence.json"
DEFAULT_CAPTURE = REPO_ROOT / "assets" / "highground-demo.png"
DEFAULT_CAPTURE_DIR = REPO_ROOT / "demo" / "artifacts" / "video-captures"
DEFAULT_OUTPUT = REPO_ROOT / "demo" / "artifacts" / "rainstorm-p5-120s.mp4"

COLORS = {
    "ink": "#16201d",
    "muted": "#65706c",
    "line": "#cfd8d3",
    "surface": "#ffffff",
    "canvas": "#e9eeeb",
    "green": "#147d68",
    "green_soft": "#dff0eb",
    "amber": "#a66609",
    "amber_soft": "#fff0d2",
    "red": "#b43a34",
    "red_soft": "#f8e3e1",
    "blue": "#27768a",
    "dark": "#101a17",
}

DECISION_LABELS = {
    "STAY": "原地守望",
    "WATCH": "增强监测",
    "PREPARE": "准备迁移",
    "MIGRATE_NOW": "建议立即迁移",
    "NO_GO": "禁止迁移",
}

CAPTURE_NAMES = {
    "STAY": "stay.png",
    "WATCH": "watch.png",
    "PREPARE": "prepare.png",
    "MIGRATE_NOW": "migrate.png",
    "AUTHORIZED": "authorized.png",
    "RECORDED_NOT_SENT": "recorded.png",
    "NO_GO": "nogo.png",
}


def find_font(explicit: Path | None) -> Path:
    candidates = [
        explicit,
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyhbd.ttc",
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    raise FileNotFoundError("No usable font found; pass --font with a CJK font path")


class Fonts:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._cache: dict[tuple[int, int], ImageFont.FreeTypeFont] = {}

    def get(self, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
        key = (size, index)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(str(self.path), size, index=index)
        return self._cache[key]


def rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    fill: str,
    outline: str | None = None,
    width: int = 1,
    radius: int = 8,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text_width(draw: ImageDraw.ImageDraw, value: str, font: ImageFont.FreeTypeFont) -> int:
    return int(draw.textbbox((0, 0), value, font=font)[2])


def fit_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    fonts: Fonts,
    max_width: int,
    start_size: int,
    minimum: int = 18,
) -> ImageFont.FreeTypeFont:
    for size in range(start_size, minimum - 1, -1):
        font = fonts.get(size)
        if text_width(draw, value, font) <= max_width:
            return font
    return fonts.get(minimum)


def repo_relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError as error:
        raise ValueError(f"Competition artifact path must be inside the repository: {path}") from error


def response_result(step: dict[str, Any]) -> dict[str, Any]:
    response = step.get("response")
    if not isinstance(response, dict):
        return {}
    result = response.get("result")
    return result if isinstance(result, dict) else {}


def state_at(
    second: float,
    scenario_steps: list[dict[str, Any]],
    evidence_steps: list[dict[str, Any]],
) -> dict[str, Any]:
    times = [float(step["at_seconds"]) for step in scenario_steps]
    index = max(0, bisect_right(times, second) - 1)
    evidence_step = evidence_steps[index]

    decision = "STAY"
    risk = "LOW"
    permission = "NONE"
    environment: dict[str, Any] = {}
    for prior_index in range(index + 1):
        prior_result = response_result(evidence_steps[prior_index])
        if prior_result.get("decision"):
            decision = str(prior_result.get("decision"))
            risk = str(prior_result.get("risk_level", risk))
            permission = str(prior_result.get("permission", permission))
        prior_environment = scenario_steps[prior_index].get("environment")
        if isinstance(prior_environment, dict):
            environment = prior_environment

    response = evidence_step.get("response")
    command_status = None
    actuator_mode = None
    if isinstance(response, dict):
        command_status = response.get("status")
        actuator_mode = response.get("actuator_mode")
    if second >= 90:
        command_step = find_unique_action_step(evidence_steps, "command")
        command_response = command_step.get("response")
        if isinstance(command_response, dict):
            command_status = command_response.get("status")
            actuator_mode = command_response.get("actuator_mode")

    capture_state = decision
    if 85 <= second < 90:
        capture_state = "AUTHORIZED"
    elif 90 <= second < 115:
        capture_state = "RECORDED_NOT_SENT"
    return {
        "index": index,
        "evidence_step": evidence_step,
        "decision": decision,
        "risk": risk,
        "permission": permission,
        "environment": environment,
        "command_status": command_status,
        "actuator_mode": actuator_mode,
        "capture_state": capture_state,
    }


def capture_for_state(captures: dict[str, Image.Image], state: str) -> Image.Image:
    if state in captures:
        return captures[state]
    adjacent_states = {
        "WATCH": "STAY",
        "PREPARE": "STAY",
        "AUTHORIZED": "MIGRATE_NOW",
        "NO_GO": "RECORDED_NOT_SENT",
    }
    adjacent = adjacent_states.get(state)
    if adjacent in captures:
        return captures[adjacent]
    return captures["FALLBACK"]


def load_capture(path: Path, target_size: tuple[int, int]) -> Image.Image:
    with Image.open(path) as source:
        image = source.convert("RGB")
    target_ratio = target_size[0] / target_size[1]
    source_ratio = image.width / image.height
    if source_ratio > target_ratio:
        new_width = int(image.height * target_ratio)
        left = (image.width - new_width) // 2
        image = image.crop((left, 0, left + new_width, image.height))
    elif source_ratio < target_ratio:
        new_height = int(image.width / target_ratio)
        top = max(0, (image.height - new_height) // 4)
        image = image.crop((0, top, image.width, top + new_height))
    return image.resize(target_size, Image.Resampling.LANCZOS)


def load_captures(
    fallback: Path,
    capture_dir: Path,
    target_size: tuple[int, int],
) -> tuple[dict[str, Image.Image], list[dict[str, Any]]]:
    if not fallback.exists():
        raise FileNotFoundError(f"Web console capture not found: {fallback}")
    fallback_image = load_capture(fallback, target_size)
    captures = {"FALLBACK": fallback_image}
    used = {fallback.resolve()}
    for state, filename in CAPTURE_NAMES.items():
        path = capture_dir / filename
        if path.exists():
            captures[state] = load_capture(path, target_size)
            used.add(path.resolve())
    sources = [
        {
            "path": repo_relative_path(path),
            "sha256": sha256(path),
            "provenance": "visual_reference_only",
        }
        for path in sorted(used, key=lambda item: str(item).casefold())
    ]
    return captures, sources


def draw_header(
    draw: ImageDraw.ImageDraw,
    fonts: Fonts,
    second: float,
    duration: float,
) -> None:
    draw.rectangle((0, 0, 1920, 82), fill=COLORS["dark"])
    rounded(draw, (36, 17, 86, 67), fill="#172724", outline="#5e726c", radius=7)
    draw.text((49, 29), "HG", font=fonts.get(21), fill="#ffffff")
    draw.text((108, 14), "高地 AI", font=fonts.get(30), fill="#ffffff")
    draw.text((108, 50), "XPENG P5 · 暴雨安全决策全流程", font=fonts.get(16), fill="#afbbb7")
    draw.ellipse((1370, 33, 1382, 45), fill="#39b795")
    draw.text((1394, 24), "本地 FastAPI / SQLite", font=fonts.get(18), fill="#d6dfdc")
    elapsed = min(int(second), int(duration))
    timer = f"{elapsed // 60:02d}:{elapsed % 60:02d} / 02:00"
    draw.text((1728, 22), timer, font=fonts.get(22), fill="#ffffff")


def draw_safety_banner(draw: ImageDraw.ImageDraw, fonts: Fonts) -> None:
    rounded(draw, (36, 96, 1884, 145), fill="#fff7e6", outline="#d5a849", width=2)
    draw.text((58, 106), "演示模式", font=fonts.get(20), fill="#704707")
    draw.text(
        (182, 107),
        "record-only  ·  vehicle_command_transmitted=false  ·  未向车辆发送任何命令",
        font=fonts.get(19),
        fill="#704707",
    )
    rounded(draw, (1654, 105, 1864, 136), fill="#ffe1dc", radius=5)
    draw.text((1681, 109), "非实车动作", font=fonts.get(17), fill=COLORS["red"])


def draw_browser_frame(
    canvas: Image.Image,
    screenshot: Image.Image,
    draw: ImageDraw.ImageDraw,
    fonts: Fonts,
    state: dict[str, Any],
) -> None:
    x, y, width, height = 36, 164, 1300, 730
    rounded(draw, (x, y, x + width, y + height), fill="#ffffff", outline="#bac7c1", radius=8)
    draw.rectangle((x + 1, y + 1, x + width - 1, y + 43), fill="#eef2f0")
    for index, color in enumerate(("#d55a52", "#d9a734", "#3ca67f")):
        draw.ellipse((x + 18 + index * 23, y + 16, x + 30 + index * 23, y + 28), fill=color)
    rounded(draw, (x + 116, y + 9, x + 1115, y + 35), fill="#ffffff", outline="#d2dad6", radius=5)
    draw.text((x + 136, y + 12), "http://127.0.0.1:8125/", font=fonts.get(14), fill="#53605b")
    reference_label = "上一轮真实 UI 参考 / 非本轮证据"
    reference_font = fit_text(draw, reference_label, fonts, 270, 15, minimum=11)
    draw.text((x + 1000, y + 12), reference_label, font=reference_font, fill=COLORS["red"])
    canvas.paste(screenshot, (x + 1, y + 44))

    rounded(
        draw,
        (x + 18, y + 360, x + 315, y + 428),
        fill="#f7f9f8",
        outline="#bac7c1",
        radius=5,
    )
    draw.text((x + 35, y + 372), "历史动态 ID 已遮挡", font=fonts.get(17), fill=COLORS["muted"])
    draw.text((x + 35, y + 397), "当前字段以右侧 evidence 为准", font=fonts.get(14), fill=COLORS["red"])
    rounded(
        draw,
        (x + 1010, y + 552, x + 1282, y + 610),
        fill="#f7f9f8",
        outline="#bac7c1",
        radius=5,
    )
    draw.text((x + 1028, y + 570), "历史事件 ID 已遮挡", font=fonts.get(15), fill=COLORS["muted"])

    decision = str(state["decision"])
    accent = COLORS["red"] if decision in {"MIGRATE_NOW", "NO_GO"} else COLORS["green"]
    rounded(draw, (x + 20, y + 58, x + 405, y + 124), fill="#ffffff", outline=accent, width=3, radius=6)
    draw.text((x + 37, y + 68), "当前真实 evidence 状态", font=fonts.get(15), fill=COLORS["muted"])
    draw.text(
        (x + 37, y + 90),
        f"{decision} · {DECISION_LABELS.get(decision, decision)}",
        font=fonts.get(22),
        fill=accent,
    )
    if state.get("command_status"):
        rounded(draw, (x + 730, y + 58, x + 1277, y + 124), fill="#fff7e6", outline="#d5a849", width=2, radius=6)
        draw.text((x + 748, y + 69), "命令接口返回", font=fonts.get(15), fill=COLORS["muted"])
        draw.text(
            (x + 748, y + 90),
            f"{state['command_status']} · {state['actuator_mode']}",
            font=fonts.get(20),
            fill="#8a5100",
        )


def draw_evidence_panel(
    draw: ImageDraw.ImageDraw,
    fonts: Fonts,
    evidence: dict[str, Any],
    state: dict[str, Any],
) -> None:
    x, y, width, height = 1360, 164, 524, 730
    rounded(draw, (x, y, x + width, y + height), fill=COLORS["surface"], outline="#bac7c1", radius=8)
    draw.text((x + 24, y + 22), "运行证据", font=fonts.get(19), fill=COLORS["muted"])
    draw.text((x + 24, y + 49), "HTTP + SQLite 审计链", font=fonts.get(27), fill=COLORS["ink"])
    draw.line((x + 24, y + 91, x + width - 24, y + 91), fill=COLORS["line"], width=2)

    decision = str(state["decision"])
    risk = str(state["risk"])
    palette = (
        (COLORS["red_soft"], COLORS["red"])
        if risk in {"HIGH", "CRITICAL"}
        else (COLORS["amber_soft"], COLORS["amber"])
        if risk == "MEDIUM"
        else (COLORS["green_soft"], COLORS["green"])
    )
    rounded(draw, (x + 24, y + 111, x + 500, y + 209), fill=palette[0], radius=7)
    draw.text((x + 43, y + 126), "决策", font=fonts.get(16), fill=COLORS["muted"])
    decision_font = fit_text(draw, decision, fonts, 290, 32)
    draw.text((x + 43, y + 151), decision, font=decision_font, fill=palette[1])
    rounded(draw, (x + 372, y + 126, x + 476, y + 162), fill="#ffffff", radius=4)
    draw.text((x + 393, y + 132), risk, font=fonts.get(16), fill=palette[1])
    draw.text((x + 372, y + 174), str(state["permission"]), font=fonts.get(14), fill=COLORS["muted"])

    step = state["evidence_step"]
    request = step.get("request") if isinstance(step.get("request"), dict) else {}
    rows = [
        ("时间节点", f"T+{int(step.get('at_seconds', 0)):03d}s"),
        ("动作", str(step.get("action", "-"))),
        ("请求", f"{request.get('method', '-')} {request.get('path', '-')}"),
        ("HTTP", str(step.get("http_status", "-"))),
        ("延迟", f"{float(step.get('latency_ms', 0)):.1f} ms"),
        ("断言", str(step.get("assertion", "-"))),
    ]
    row_y = y + 236
    for label, value in rows:
        draw.text((x + 25, row_y), label, font=fonts.get(16), fill=COLORS["muted"])
        value_font = fit_text(draw, value, fonts, 354, 17, 13)
        draw.text((x + 145, row_y), value, font=value_font, fill=COLORS["ink"])
        draw.line((x + 24, row_y + 30, x + width - 24, row_y + 30), fill="#e7ece9", width=1)
        row_y += 47

    environment = state["environment"]
    rounded(draw, (x + 24, y + 525, x + 500, y + 614), fill="#f2f6f4", radius=6)
    draw.text((x + 43, y + 540), "传感器快照", font=fonts.get(16), fill=COLORS["muted"])
    draw.text(
        (x + 43, y + 567),
        f"雨强 {environment.get('rainfall_mm_h', '-')} mm/h   水位 {environment.get('water_level_cm', '-')} cm",
        font=fonts.get(18),
        fill=COLORS["ink"],
    )

    run_id = str(evidence.get("run_id", "-"))
    rounded(draw, (x + 24, y + 632, x + 500, y + 706), fill=COLORS["dark"], radius=6)
    draw.text((x + 43, y + 645), f"run_id  {run_id}", font=fonts.get(15), fill="#dce6e2")
    draw.text(
        (x + 43, y + 672),
        "record_only=true  ·  transmitted=false",
        font=fonts.get(15),
        fill="#66d2b3",
    )


def draw_timeline(
    draw: ImageDraw.ImageDraw,
    fonts: Fonts,
    second: float,
    duration: float,
    steps: list[dict[str, Any]],
) -> None:
    left, right = 54, 1866
    line_y = 973
    draw.text((36, 919), "120 秒本地 HTTP 运行时间线", font=fonts.get(21), fill=COLORS["ink"])
    draw.text((1630, 921), "场景固定 · 逐项断言", font=fonts.get(16), fill=COLORS["muted"])
    draw.line((left, line_y, right, line_y), fill="#c5d0cb", width=7)
    progress_right = left + int((right - left) * min(1, second / duration))
    draw.line((left, line_y, progress_right, line_y), fill=COLORS["green"], width=7)

    for step in steps:
        at = float(step["at_seconds"])
        x = left + int((right - left) * at / duration)
        completed = second >= at
        fill = COLORS["green"] if completed else "#ffffff"
        outline = COLORS["green"] if completed else "#9caaa4"
        draw.ellipse((x - 9, line_y - 9, x + 9, line_y + 9), fill=fill, outline=outline, width=3)
        if at in {0, 20, 45, 70, 90, 115, 120}:
            label = f"{int(at) // 60:02d}:{int(at) % 60:02d}"
            label_width = text_width(draw, label, fonts.get(14))
            draw.text((x - label_width // 2, line_y + 17), label, font=fonts.get(14), fill=COLORS["muted"])

    marker_x = left + int((right - left) * min(1, second / duration))
    draw.polygon(
        [(marker_x, line_y - 22), (marker_x - 8, line_y - 34), (marker_x + 8, line_y - 34)],
        fill=COLORS["green"],
    )
    draw.text(
        (36, 1030),
        "证据来源：demo/run_scenario.py  ·  所有状态来自本地 HTTP 返回  ·  单次授权令牌仅保留 SHA-256",
        font=fonts.get(16),
        fill=COLORS["muted"],
    )


def draw_frame(
    *,
    second: float,
    duration: float,
    scenario: dict[str, Any],
    evidence: dict[str, Any],
    captures: dict[str, Image.Image],
    fonts: Fonts,
) -> Image.Image:
    canvas = Image.new("RGB", (1920, 1080), COLORS["canvas"])
    draw = ImageDraw.Draw(canvas)
    scenario_steps = scenario["steps"]
    evidence_steps = evidence["steps"]
    state = state_at(second, scenario_steps, evidence_steps)
    screenshot = capture_for_state(captures, str(state["capture_state"]))
    draw_header(draw, fonts, second, duration)
    draw_safety_banner(draw, fonts)
    draw_browser_frame(canvas, screenshot, draw, fonts, state)
    draw_evidence_panel(draw, fonts, evidence, state)
    draw_timeline(draw, fonts, second, duration, scenario_steps)
    return canvas


def probe_video(path: Path) -> dict[str, Any]:
    reader = imageio_ffmpeg.read_frames(str(path), pix_fmt="rgb24")
    metadata = next(reader)
    reader.close()
    frame_count, counted_duration = imageio_ffmpeg.count_frames_and_secs(str(path))
    size = metadata.get("size") or metadata.get("source_size")
    return {
        "codec": metadata.get("codec"),
        "pixel_format": metadata.get("pix_fmt"),
        "width": int(size[0]),
        "height": int(size[1]),
        "fps": float(metadata.get("fps", 0)),
        "duration_seconds": float(metadata.get("duration", counted_duration)),
        "frame_count": int(frame_count),
    }


def render_video(args: argparse.Namespace) -> dict[str, Any]:
    scenario = load_json(args.scenario)
    evidence = load_json(args.evidence)
    validate_inputs(scenario, evidence)
    duration = float(scenario["duration_seconds"])
    frame_count = int(round(duration * args.fps))
    if args.width != 1920 or args.height != 1080:
        raise ValueError("The competition render is fixed at 1920x1080")

    fonts = Fonts(find_font(args.font))
    captures, capture_sources = load_captures(
        args.capture,
        args.captures_dir,
        (1298, 685),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    writer = imageio_ffmpeg.write_frames(
        str(args.output),
        (args.width, args.height),
        fps=args.fps,
        codec="libx264",
        pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p",
        quality=8,
        macro_block_size=2,
        ffmpeg_log_level="warning",
        output_params=[
            "-preset",
            "medium",
            "-movflags",
            "+faststart",
            "-metadata",
            "title=HighGround AI XPENG P5 120s rainstorm demo",
            "-metadata",
            "comment=record-only; vehicle_command_transmitted=false",
        ],
    )
    writer.send(None)
    try:
        for frame_index in range(frame_count):
            second = frame_second(frame_index, frame_count, args.fps, duration)
            frame = draw_frame(
                second=second,
                duration=duration,
                scenario=scenario,
                evidence=evidence,
                captures=captures,
                fonts=fonts,
            )
            writer.send(frame.tobytes())
            if frame_index % (args.fps * 10) == 0:
                print(f"rendering {second:05.1f}s / {duration:.1f}s")
    finally:
        writer.close()

    media = probe_video(args.output)
    if not math.isclose(media["duration_seconds"], duration, abs_tol=0.1):
        raise ValueError(f"Rendered duration is {media['duration_seconds']}, expected {duration}")
    if media["width"] != args.width or media["height"] != args.height:
        raise ValueError("Rendered dimensions do not match the requested dimensions")
    if media["codec"] != "h264":
        raise ValueError(f"Rendered codec is {media['codec']}, expected h264")

    manifest = {
        "schema_version": 1,
        "output": repo_relative_path(args.output),
        "sha256": sha256(args.output),
        "size_bytes": args.output.stat().st_size,
        "media": media,
        "scenario": repo_relative_path(args.scenario),
        "scenario_sha256": sha256(args.scenario),
        "evidence": repo_relative_path(args.evidence),
        "evidence_sha256": sha256(args.evidence),
        "evidence_run_id": evidence["run_id"],
        "record_only": evidence["record_only"],
        "vehicle_command_transmitted": evidence["vehicle_command_transmitted"],
        "captures_are_evidence": False,
        "dynamic_fields_source": "evidence",
        "capture_sources": capture_sources,
    }
    manifest_path = args.output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the canonical 120-second competition demo from asserted HTTP evidence."
    )
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--captures-dir", type=Path, default=DEFAULT_CAPTURE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--font", type=Path)
    parser.add_argument("--fps", type=int, default=24, choices=(24, 25, 30))
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    return parser.parse_args()


def main() -> int:
    try:
        render_video(parse_args())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"video render failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
