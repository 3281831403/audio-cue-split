"""audio-cue-split 的验收测试：初始 15 条全红，不得修改。

每个测试都直接调用尚未实现的桩函数；当前失败原因均为
``NotImplementedError``。测试只验证公开契约，不修改实现。
"""

import pytest

from cue.splitter import (
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


def _planned_tracks():
    """构造一组首尾相接、覆盖 0..300 帧的规划轨。"""
    return [
        {"number": 1, "title": "第一轨", "start_frame": 0, "end_frame": 100},
        {"number": 2, "title": "第二轨", "start_frame": 100, "end_frame": 220},
        {"number": 3, "title": "第三轨", "start_frame": 220, "end_frame": 300},
    ]


def _cue_a():
    """两份 CUE 中的第一份：两轨，第二轨从第 100 帧开始。"""
    return [
        'TITLE "专辑 A"',
        'FILE "a.wav" WAVE',
        '  TRACK 01 AUDIO',
        '    TITLE "A1"',
        '    INDEX 01 00:00:00',
        '  TRACK 02 AUDIO',
        '    TITLE "A2"',
        '    INDEX 01 00:04:00',
    ]


def _cue_b():
    """两份 CUE 中的第二份：两轨，第二轨从第 250 帧开始。"""
    return [
        'TITLE "专辑 B"',
        '  TRACK 01 AUDIO',
        '    TITLE "B1"',
        '    INDEX 01 00:00:00',
        '  TRACK 02 AUDIO',
        '    TITLE "B2"',
        '    INDEX 01 00:10:00',
    ]


def test_timecode_uses_actual_fps_not_75():
    """缺陷 1：MM:SS:FF 必须按传入 fps 换算，不能固定按 75 帧。"""
    assert timecode_to_frames("00:10:00", 25) == 250
    assert timecode_to_frames("01:00:00", 30) == 1800
    assert abs(timecode_to_frames("00:00:12", 24) - 12) <= 1


def test_timecode_rejects_ff_at_or_above_fps():
    """缺陷 2：FF 必须小于 fps，否则时间码溢出。"""
    for fps in (24, 25, 30):
        with pytest.raises(CueError):
            timecode_to_frames(f"00:00:{fps:02d}", fps)

    assert timecode_to_frames("00:00:23", 24) == 23


def test_last_track_ends_at_audio_tail():
    """缺陷 3：最后一轨结束必须取总帧数，不能等下一个索引。"""
    tracks = [
        {"number": 1, "title": "A", "start_frame": 0},
        {"number": 2, "title": "B", "start_frame": 100},
        {"number": 3, "title": "C", "start_frame": 200},
    ]

    planned = plan_tracks(tracks, 500)

    assert [track["start_frame"] for track in planned] == [0, 100, 200]
    assert [track["end_frame"] for track in planned] == [100, 200, 500]
    assert planned[-1]["end_frame"] == 500


def test_gap_does_not_grow_total_length():
    """缺陷 4：插入间隙后总跨度不能变长，间隙应由前轨尾部让出。"""
    before = _planned_tracks()

    gapped = apply_gap(before, 10)

    assert [track["start_frame"] for track in gapped] == [0, 100, 220]
    assert [track["end_frame"] for track in gapped] == [90, 210, 300]
    assert gapped[0]["end_frame"] + 10 == gapped[1]["start_frame"]
    assert gapped[1]["end_frame"] + 10 == gapped[2]["start_frame"]
    assert gapped[-1]["end_frame"] - gapped[0]["start_frame"] == 300


def test_filename_sanitizer_blocks_path_escape():
    """缺陷 5：文件名不能直接拼接含斜杠的标题，必须去掉路径分隔符。"""
    name = sanitize_name("../../AC/DC: Live?", 7)

    assert name == "07 - ACDC Live"
    assert "/" not in name
    assert "\\" not in name
    assert ".." not in name


def test_pregap_adapts_to_quiet_and_loud_audio():
    """缺陷 6：静音阈值要随轨峰值自适应，轻声和大声都要检测到。"""
    audio = [0] * 30 + [3] * 70 + [0] * 30 + [9000] * 70
    tracks = [
        {"number": 1, "title": "轻声", "start_frame": 0, "end_frame": 100},
        {"number": 2, "title": "大声", "start_frame": 100, "end_frame": 200},
    ]

    offsets = detect_pregap(tracks, audio)

    assert len(offsets) == 2
    assert abs(offsets[0] - 30) <= 1
    assert abs(offsets[1] - 30) <= 1


def test_validate_reports_overlapping_tracks():
    """缺陷 7：轨重叠必须报错，不能导出后重复同一段音频。"""
    tracks = [
        {"number": 1, "title": "A", "start_frame": 0, "end_frame": 100},
        {"number": 2, "title": "B", "start_frame": 90, "end_frame": 180},
    ]

    issues = validate_tracks(tracks, 180)

    assert issues
    assert any("重叠" in issue for issue in issues)


def test_validate_reports_zero_duration_track():
    """缺陷 8：时长为 0 的轨必须报错，不能生成空文件。"""
    tracks = [
        {"number": 1, "title": "A", "start_frame": 0, "end_frame": 100},
        {"number": 2, "title": "空轨", "start_frame": 100, "end_frame": 100},
        {"number": 3, "title": "C", "start_frame": 100, "end_frame": 180},
    ]

    issues = validate_tracks(tracks, 180)

    assert issues
    assert any("时长" in issue for issue in issues)


def test_batch_does_not_share_tracks_between_cues():
    """缺陷 9：批量不能共用轨列表，后一份 CUE 不能串入前一份的轨。"""
    batch = split_batch([_cue_a(), _cue_b()], 300, 25)
    entries = batch["cues"]

    assert [track["title"] for track in entries[0]["tracks"]] == ["A1", "A2"]
    assert [track["title"] for track in entries[1]["tracks"]] == ["B1", "B2"]
    assert [track["start_frame"] for track in entries[0]["tracks"]] == [0, 100]
    assert [track["start_frame"] for track in entries[1]["tracks"]] == [0, 250]


def test_zero_fps_raises_cue_error():
    """缺陷 10：fps <= 0 必须在换算前报错，不能除零。"""
    with pytest.raises(CueError):
        timecode_to_frames("00:00:00", 0)

    with pytest.raises(CueError):
        timecode_to_frames("00:00:00", -25)


def test_single_track_cue_covers_whole_audio():
    """组合 1：单轨 CUE 从 0 开始，一直覆盖到整段音频片尾。"""
    cue = [
        'TRACK 01 AUDIO',
        '  TITLE "Solo"',
        '  INDEX 01 00:00:00',
    ]

    batch = split_batch([cue], 500, 25)
    entry = batch["cues"][0]
    tracks = entry["tracks"]

    assert len(tracks) == 1
    assert tracks[0]["start_frame"] == 0
    assert tracks[0]["end_frame"] == 500
    assert entry["total_frames"] == 500


def test_zero_gap_keeps_tracks_contiguous():
    """组合 2：gap=0 时轨首尾相接，不产生空隙。"""
    tracks = _planned_tracks()

    gapped = apply_gap(tracks, 0)

    assert [track["start_frame"] for track in gapped] == [0, 100, 220]
    assert [track["end_frame"] for track in gapped] == [100, 220, 300]
    for left, right in zip(gapped, gapped[1:]):
        assert left["end_frame"] == right["start_frame"]


def test_empty_cue_raises_cue_error():
    """组合 3：空 CUE 或缺 TRACK 的 CUE 必须抛 CueError。"""
    with pytest.raises(CueError):
        parse_cue([])

    with pytest.raises(CueError):
        parse_cue(['TITLE "只有专辑名"'])


def test_sanitized_filename_keeps_chinese_without_slash():
    """组合 4：安全文件名保留中文，同时不含任何路径分隔符。"""
    name = sanitize_name("中文 / 标题", 2)

    assert name == "02 - 中文 标题"
    assert "/" not in name
    assert "\\" not in name


def test_batch_preserves_order_on_success():
    """组合 5：批量全部成功时，CUE 顺序和轨内顺序都不变。"""
    batch = split_batch([_cue_b(), _cue_a()], 300, 25)

    groups = [
        [track["title"] for track in entry["tracks"]]
        for entry in batch["cues"]
    ]

    assert groups == [["B1", "B2"], ["A1", "A2"]]
