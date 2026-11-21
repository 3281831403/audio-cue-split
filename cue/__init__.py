"""CUE 分轨工具包。

核心实现位于 :mod:`cue.splitter`。
"""

from .splitter import (
    CueError,
    apply_gap,
    detect_pregap,
    parse_cue,
    plan_tracks,
    sanitize_name,
    split_batch,
    timecode_to_frames,
    validate_tracks,
)

__all__ = [
    "CueError",
    "apply_gap",
    "detect_pregap",
    "parse_cue",
    "plan_tracks",
    "sanitize_name",
    "split_batch",
    "timecode_to_frames",
    "validate_tracks",
]
