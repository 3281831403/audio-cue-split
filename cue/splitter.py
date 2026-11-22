"""按 CUE 把整轨音频拆成多轨：时间码、规划、间隙、命名与批量校验。

公共约定
========
时间轴
    统一使用“帧”（frame）。``MM:SS:FF`` 中的 ``FF`` 必须落在
    ``0 .. fps - 1``；帧级比较允许 1 帧容差。
音频
    不读取真实音频文件。``audio_len`` 表示采样点总数，``detect_pregap``
    接收整数幅度采样点序列；本练习约定一个采样点对应时间轴上的一个
    帧，不做采样率换算。
CUE
    用文本行 ``list[str]`` 表示，不访问文件系统；每行只做内存解析。
异常
    非法输入、非法时间码、缺失 INDEX 01、无法恢复的轨结构等，统一抛
    :class:`CueError`。
约束
    仅使用标准库 ``bisect``、``re``；离线、不使用随机数、不调用 sleep。

本模块初始为练习桩：8 个函数均抛出 ``NotImplementedError``，等待实现。
"""

from __future__ import annotations

import bisect
import re


FRAME_TOLERANCE = 1
"""帧级比较容差。"""

ILLEGAL_FILENAME_CHARS = frozenset('<>:"/\\|?*')
"""Windows 文件名中不允许出现的字符；路径分隔符也在其中。"""


_TRACK_RE = re.compile(r"^\s*TRACK\s+(\d+)\b", re.IGNORECASE)
_TITLE_RE = re.compile(r'^\s*TITLE\s+"(.*)"\s*$', re.IGNORECASE)
_INDEX_RE = re.compile(r"^\s*INDEX\s+(\d+)\s+(\S+)\s*$", re.IGNORECASE)
_TIMECODE_RE = re.compile(r"^(\d{2,}):(\d{2}):(\d{2})$")
_WHITESPACE_RE = re.compile(r"\s+")


class CueError(ValueError):
    """CUE 文本、时间码、轨规划或批量输入非法时抛出的异常。"""


def _require_int(value, name: str, *, minimum: int | None = None) -> int:
    """校验整数参数（布尔值不算整数），可选下界。"""

    if isinstance(value, bool) or not isinstance(value, int):
        raise CueError(f"{name} 必须是整数")
    if minimum is not None and value < minimum:
        raise CueError(f"{name} 必须不小于 {minimum}")
    return value


def _require_track(track, key: str):
    """取轨字典中的字段，缺失或类型错误时统一抛 :class:`CueError`。"""

    if not isinstance(track, dict):
        raise CueError("轨必须是字典")
    if key not in track:
        raise CueError(f"轨缺少字段 {key}")
    return track[key]


def parse_cue(text: list[str]) -> list[dict]:
    """解析 CUE 文本行，返回按源顺序排列的轨列表。

    契约
    ----
    * ``text`` 为 CUE 文本行列表；空列表、只有注释/TITLE 而没有
      ``TRACK``、或完全没有 ``INDEX 01`` 时抛 :class:`CueError`。
    * 识别 ``TRACK <数字> ...``、轨内的 ``TITLE "..."`` 和
      ``INDEX 01 MM:SS:FF``。``FILE``、专辑级 ``TITLE``、``INDEX 00``
      及其他行忽略。
    * 每条轨必须带有 ``INDEX 01``；缺失时抛 :class:`CueError`，不能
      猜测起点。轨号重复也抛 :class:`CueError`。
    * 返回 ``[{"number": int, "title": str, "start": "MM:SS:FF"}, ...]``，
      保持源文件顺序。轨内没有 ``TITLE`` 时使用 ``"Track {number:02d}"``。
    * 本函数只解析文本，不把时间码换算成帧，也不修改 ``text``。
    """

    if not isinstance(text, (list, tuple)):
        raise CueError("CUE 文本必须是行列表")

    tracks: list[dict] = []
    seen_numbers: set[int] = set()
    current: dict | None = None

    for raw_line in text:
        if not isinstance(raw_line, str):
            raise CueError("CUE 文本的每一行都必须是字符串")

        match = _TRACK_RE.match(raw_line)
        if match:
            number = int(match.group(1))
            if number in seen_numbers:
                raise CueError(f"轨号重复：{number}")
            seen_numbers.add(number)
            # 每条轨独立持有自己的状态，绝不复用上一轨的字段。
            current = {"number": number, "title": None, "start": None}
            tracks.append(current)
            continue

        match = _TITLE_RE.match(raw_line)
        if match:
            # 轨级 TITLE 生效；专辑级 TITLE（首个 TRACK 之前）忽略。
            if current is not None and current["title"] is None:
                current["title"] = match.group(1)
            continue

        match = _INDEX_RE.match(raw_line)
        if match:
            index_number = int(match.group(1))
            # 只认 INDEX 01；INDEX 00（前奏索引）等一律忽略。
            if (
                index_number == 1
                and current is not None
                and current["start"] is None
            ):
                current["start"] = match.group(2)
            continue

        # FILE、REM 等其他行忽略。

    if not tracks:
        raise CueError("CUE 中没有任何 TRACK")

    for track in tracks:
        if track["start"] is None:
            raise CueError(f"轨 {track['number']} 缺少 INDEX 01")
        if track["title"] is None:
            track["title"] = f"Track {track['number']:02d}"

    return tracks


def timecode_to_frames(tc: str, fps: int) -> int:
    """把 ``MM:SS:FF`` 时间码按给定帧率换算成绝对帧号。

    契约
    ----
    * ``tc`` 必须严格匹配 ``MM:SS:FF``：分钟为两位或更多位十进制，
      秒为 ``00..59``，帧为 ``00..fps-1``。
    * ``fps`` 必须为正整数；``fps <= 0``、非整数或布尔值都抛
      :class:`CueError`，不得执行除法。
    * 帧字段 ``FF >= fps`` 时抛 :class:`CueError`，不能把溢出的帧数
      静默算进秒或分钟。
    * 换算公式为 ``(minutes * 60 + seconds) * fps + frames``；帧率由
      ``fps`` 决定，绝不固定按 75 帧/秒解析。结果允许 1 帧误差。
    """

    fps = _require_int(fps, "fps", minimum=1)
    if not isinstance(tc, str):
        raise CueError("时间码必须是字符串")

    match = _TIMECODE_RE.match(tc)
    if not match:
        raise CueError(f"非法时间码：{tc!r}")

    minutes = int(match.group(1))
    seconds = int(match.group(2))
    frames = int(match.group(3))

    if seconds > 59:
        raise CueError(f"非法时间码秒字段：{tc!r}")
    # FF 必须落在 0..fps-1；溢出时不能静默进位到秒或分钟。
    if frames >= fps:
        raise CueError(f"帧字段 {frames} 超出帧率 {fps}：{tc!r}")

    return (minutes * 60 + seconds) * fps + frames


def plan_tracks(tracks: list[dict], total_frames: int) -> list[dict]:
    """为归一化轨生成起止帧，最后一轨必须覆盖到片尾。

    契约
    ----
    * ``tracks`` 为按起点升序的归一化轨，每项至少包含 ``number``、
      ``title``、``start_frame``；``total_frames`` 为整轨总帧数（``>= 0``）。
    * 返回新列表，保留原顺序和原字段，并为每项补上 ``start_frame`` 与
      ``end_frame``。除最后一轨外，``end_frame`` 等于下一轨的
      ``start_frame``；最后一轨的 ``end_frame`` **必须**等于
      ``total_frames``，不能使用不存在的“下一个索引”，片尾不得丢失。
    * 首轨起点可以大于 0；所有起点必须非负、不超过 ``total_frames``
      且单调不降，否则抛 :class:`CueError`。
    * ``tracks`` 为空时返回 ``[]``；不得修改输入。
    """

    total_frames = _require_int(total_frames, "total_frames", minimum=0)
    if not isinstance(tracks, (list, tuple)):
        raise CueError("tracks 必须是列表")

    starts: list[int] = []
    for track in tracks:
        start = _require_track(track, "start_frame")
        start = _require_int(start, "start_frame", minimum=0)
        if start > total_frames:
            raise CueError(f"轨 {track.get('number')} 起点 {start} 超出总长 {total_frames}")
        starts.append(start)

    for previous, current in zip(starts, starts[1:]):
        if current < previous:
            raise CueError("轨起点必须单调不降")

    planned: list[dict] = []
    for index, track in enumerate(tracks):
        # 复制轨字典，保留原有字段；不修改输入。
        planned_track = dict(track)
        planned_track["start_frame"] = starts[index]
        # 除最后一轨外，结束于下一轨起点；最后一轨必须覆盖到片尾。
        if index + 1 < len(starts):
            planned_track["end_frame"] = starts[index + 1]
        else:
            planned_track["end_frame"] = total_frames
        planned.append(planned_track)

    return planned


def apply_gap(tracks: list[dict], gap_frames: int) -> list[dict]:
    """在相邻轨之间留出静音间隙，同时保持整段总跨度不变。

    契约
    ----
    * ``tracks`` 为 :func:`plan_tracks` 生成的带 ``start_frame`` /
      ``end_frame`` 的轨；``gap_frames`` 为 ``>= 0`` 的整数。
    * 对每一对相邻轨，从**前一轨尾部**让出 ``gap_frames`` 帧作为间隙：
      前一轨 ``end_frame`` 减去 ``gap_frames``，后一轨 ``start_frame``
      保持不变。这样第 i 轨结束后到第 i+1 轨开始前恰好是
      ``gap_frames`` 帧静音。
    * 最后一轨的 ``end_frame`` 与所有轨的 ``start_frame`` 均不变，因此
      ``最后一轨 end - 第一轨 start`` 在调用前后完全相等，总长不会变长。
    * 任一非末轨的时长小于 ``gap_frames`` 时无法让出完整间隙，抛
      :class:`CueError`。``gap_frames == 0`` 时返回等值的新列表。
    * 不修改输入；``tracks`` 为空时返回 ``[]``。
    """

    gap_frames = _require_int(gap_frames, "gap_frames", minimum=0)
    if not isinstance(tracks, (list, tuple)):
        raise CueError("tracks 必须是列表")
    if not tracks:
        return []

    gapped: list[dict] = []
    for index, track in enumerate(tracks):
        start = _require_int(
            _require_track(track, "start_frame"), "start_frame", minimum=0
        )
        end = _require_int(
            _require_track(track, "end_frame"), "end_frame", minimum=0
        )
        gapped_track = dict(track)

        if index + 1 < len(tracks):
            # 非末轨从自己的尾部让出完整间隙，后一轨起点不动。
            if end - start < gap_frames:
                raise CueError(
                    f"轨 {track.get('number')} 时长不足以让出 {gap_frames} 帧间隙"
                )
            gapped_track["end_frame"] = end - gap_frames
        # 末轨 end 不变；所有 start 均不变，因此总跨度保持不变。
        gapped_track["start_frame"] = start
        gapped.append(gapped_track)

    return gapped


def sanitize_name(title: str, index: int) -> str:
    """根据标题与轨号生成安全、可读且保留中文的文件名主干。

    契约
    ----
    * ``index`` 必须是正整数；``title`` 必须是字符串，否则抛
      :class:`CueError`。
    * 删除 Windows 非法字符 ``< > : " / \\ | ? *`` 与 ASCII 控制字符，
      其中 ``/``、``\\`` 绝不能保留，防止路径逃逸或目录穿越。
    * 将连续空白折叠为一个半角空格；去掉首尾的空格和点，避免隐藏
      文件或 ``..`` 路径片段。中文、字母、数字及其他正常 Unicode
      字符原样保留。
    * 清理后为空时使用 ``"Track"``。返回格式固定为
      ``f"{index:02d} - {cleaned}"``，例如 ``"07 - ACDC Live"``。
    * 返回值是纯文件名主干，不含目录，也不访问文件系统。
    """

    index = _require_int(index, "index", minimum=1)
    if not isinstance(title, str):
        raise CueError("title 必须是字符串")

    # 删除 Windows 非法字符（含 / 与 \，阻断路径逃逸）与 ASCII 控制字符。
    cleaned_chars = []
    for char in title:
        if char in ILLEGAL_FILENAME_CHARS:
            continue
        code = ord(char)
        if code < 32 or code == 127:
            continue
        cleaned_chars.append(char)
    cleaned = "".join(cleaned_chars)

    # 连续空白折叠为单个半角空格，并去掉首尾的空格与点，
    # 避免隐藏文件或残留 ".." 路径片段。
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip(" .")

    if not cleaned:
        cleaned = "Track"

    return f"{index:02d} - {cleaned}"


def detect_pregap(tracks: list[dict], audio: list[int]) -> list[int]:
    """检测每条轨开头的静音前奏长度，返回需要跳过的偏移帧。

    契约
    ----
    * ``tracks`` 为带 ``start_frame`` / ``end_frame`` 的规划轨；``audio``
      为整数幅度采样点序列，一个采样点对应一帧，``0`` 表示静音。
    * 对每条轨，只在 ``[start_frame, end_frame)`` 内检查开头连续的静音
      采样，遇到第一个超过阈值的采样即停止，返回该段长度（单位：帧）。
    * 阈值必须按当前轨的峰值自适应：约为 ``max(1, peak * 0.02)``，
      ``peak`` 是该轨区间内 ``abs(sample)`` 的最大值。不得使用固定
      绝对阈值，否则轻声轨会漏检、大声轨会误检。整段全静音时返回该
      轨完整时长。
    * 返回与 ``tracks`` 等长的 ``list[int]``，顺序一致；无静音前奏为
      ``0``。结果允许 1 帧误差。
    * 轨边界越出 ``audio`` 范围时抛 :class:`CueError`；不修改输入。
    """

    if not isinstance(audio, (list, tuple)):
        raise CueError("audio 必须是采样点序列")
    if not isinstance(tracks, (list, tuple)):
        raise CueError("tracks 必须是列表")

    offsets: list[int] = []
    audio_len = len(audio)

    for track in tracks:
        start = _require_int(
            _require_track(track, "start_frame"), "start_frame", minimum=0
        )
        end = _require_int(
            _require_track(track, "end_frame"), "end_frame", minimum=0
        )
        if end < start:
            raise CueError("轨结束早于起点")
        if end > audio_len:
            raise CueError(
                f"轨区间 [{start}, {end}) 越出音频范围 {audio_len}"
            )

        segment = audio[start:end]
        # 自适应阈值：按本轨峰值缩放，轻声轨不会漏检、大声轨不会误检。
        peak = 0
        for sample in segment:
            magnitude = abs(sample)
            if magnitude > peak:
                peak = magnitude
        threshold = max(1, peak * 0.02)

        offset = 0
        for sample in segment:
            if abs(sample) > threshold:
                break
            offset += 1
        offsets.append(offset)

    return offsets


def validate_tracks(tracks: list[dict], total_frames: int) -> list[str]:
    """检查轨列表的重叠、越界与零时长问题，返回人类可读的问题列表。

    契约
    ----
    * 返回按轨顺序排列的字符串列表；没有任何问题时返回 ``[]``，本函数
      不抛异常来表示轨问题。
    * 越界：``start_frame < 0``、``end_frame > total_frames`` 或
      ``start_frame > total_frames``，问题文本必须包含 ``"越界"``。
    * 零时长：``end_frame == start_frame``，问题文本必须包含
      ``"时长"``；``end_frame < start_frame`` 同样视为非法时长。
    * 重叠：某轨 ``start_frame`` 小于前一轨的 ``end_frame``，问题文本
      必须包含 ``"重叠"``。重叠会导致导出后同一段音频出现两次。
    * ``total_frames`` 为负时抛 :class:`CueError`；空轨列表返回 ``[]``；
      不修改输入，也不从列表里静默删除任何轨。
    """

    total_frames = _require_int(total_frames, "total_frames", minimum=0)
    if not isinstance(tracks, (list, tuple)):
        raise CueError("tracks 必须是列表")

    issues: list[str] = []
    previous_end: int | None = None

    for track in tracks:
        number = track.get("number") if isinstance(track, dict) else None
        start = _require_int(
            _require_track(track, "start_frame"), "start_frame"
        )
        end = _require_int(_require_track(track, "end_frame"), "end_frame")

        # 越界：起止任一超出 [0, total_frames]。
        if start < 0 or start > total_frames or end > total_frames:
            issues.append(
                f"轨 {number} 越界：[{start}, {end}) 不在 [0, {total_frames}] 内"
            )

        # 零时长或反向时长：会导出空文件。
        if end <= start:
            issues.append(f"轨 {number} 时长非法：{end - start} 帧")

        # 重叠：本轨起点早于前一轨结束，导出后会重复同一段音频。
        if previous_end is not None and start < previous_end:
            issues.append(f"轨 {number} 与前一轨重叠：起点 {start} < 前一轨结束 {previous_end}")

        previous_end = end

    return issues


def split_batch(
    cues: list[list[str]],
    audio_len: int,
    fps: int,
) -> dict:
    """批量按 CUE 分轨；每份 CUE 相互隔离，任一非法则整体失败。

    契约
    ----
    * ``cues`` 为有序的 CUE 文本行列表的列表；``audio_len`` 为总采样
      点数（正整数，本练习按帧计）；``fps`` 为正整数。
    * 每份 CUE 都独立执行 ``parse_cue -> timecode_to_frames ->
      plan_tracks -> validate_tracks -> sanitize_name``，不得复用、共享
      或原地追加同一个轨列表，否则前一份 CUE 的轨会串到后一份。
    * 返回::

        {
          "cues": [
            {
              "tracks": [
                {"number": int, "title": str, "filename": str,
                 "start_frame": int, "end_frame": int},
                ...
              ],
              "track_count": int,
              "total_frames": int,
            },
            ...
          ],
          "cue_count": int,
          "track_count": int,
          "total_frames": int,
        }

      顺序与输入 ``cues`` 完全一致；单轨 CUE 的 ``start_frame`` 为 0，
      ``end_frame`` 为 ``audio_len``。``cues`` 为空时返回空结构。
    * 任一 CUE 解析失败、时间码非法、轨重叠、越界或时长为 0，都必须在
      返回前抛 :class:`CueError`；调用方不会拿到半成品，输入也不会被
      修改。批量全部成功时，各 CUE 的轨列表必须是彼此独立的对象。
    """

    audio_len = _require_int(audio_len, "audio_len", minimum=1)
    fps = _require_int(fps, "fps", minimum=1)
    if not isinstance(cues, (list, tuple)):
        raise CueError("cues 必须是列表")

    entries: list[dict] = []
    batch_track_count = 0

    for cue_index, cue in enumerate(cues):
        # 每份 CUE 都从零开始，构建全新的轨列表，绝不复用其它 CUE 的状态。
        parsed = parse_cue(cue)
        normalized = [
            {
                "number": track["number"],
                "title": track["title"],
                "start_frame": timecode_to_frames(track["start"], fps),
            }
            for track in parsed
        ]
        planned = plan_tracks(normalized, audio_len)

        issues = validate_tracks(planned, audio_len)
        if issues:
            raise CueError(
                f"第 {cue_index + 1} 份 CUE 轨结构非法：" + "；".join(issues)
            )

        tracks = [
            {
                "number": track["number"],
                "title": track["title"],
                "filename": sanitize_name(track["title"], track["number"]),
                "start_frame": track["start_frame"],
                "end_frame": track["end_frame"],
            }
            for track in planned
        ]

        entries.append(
            {
                "tracks": tracks,
                "track_count": len(tracks),
                "total_frames": audio_len,
            }
        )
        batch_track_count += len(tracks)

    return {
        "cues": entries,
        "cue_count": len(entries),
        "track_count": batch_track_count,
        "total_frames": audio_len,
    }
