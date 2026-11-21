# audio-cue-split

## 题面

整轨抓轨文件按 CUE 分轨后，最后一首丢了片尾、文件名还带斜杠：时间码 `MM:SS:FF` 固定按 75 帧解析，非 CD 帧率时整体偏移；`FF` 不小于 `fps` 也不校验，时间溢出；最后一轨的结束时间误用下一个索引，片尾丢失；轨间插入间隙后总长变长；文件名直接拼接标题，含 `/` 时路径逃逸；静音检测使用固定阈值，不同响度漏检；轨重叠不报错，导出后重复；时长为 0 的轨不剔除，产生空文件；批量分轨共用轨列表，CUE 之间互相串轨；`fps=0` 不校验，直接除零。cue/splitter.py 是核心，tests/test_splitter.py 15个红。要修的是：按实际 `fps` 解析并校验 `MM:SS:FF`，让最后一轨覆盖到片尾，插入间隙不改变总时长，生成不含路径分隔符的安全文件名，按自适应阈值检测静音前奏，拒绝重叠、越界和零时长轨，隔离每份 CUE 的轨列表并在任一非法时整体回滚，最终让 15 个测试全部转绿。

## 运行方式

在仓库根目录执行：

```powershell
python -m pytest tests -q
```

初始（未实现）状态下必须恰好输出 `15 failed`，且没有 collection error、import error 或 syntax error：

```text
15 failed
```

15 个测试都直接调用 `cue/splitter.py` 中的桩函数，因此当前失败原因均为 `NotImplementedError`；实现完成后应输出 `15 passed`。

## 字段表

| 字段 | 取值 |
| --- | --- |
| 所属领域 | 游戏、图形与多媒体 |
| 子方向 | 音视频处理 |
| 任务类型 | Bugfix |
| 语言/框架 | Python 3, pytest |
| 环境可复现等级 | 无外部依赖 |

## 目录结构

```text
audio-cue-split/
├── README.md
├── conftest.py
├── .gitignore
├── cue/
│   ├── __init__.py
│   └── splitter.py
└── tests/
    └── test_splitter.py
```

`conftest.py` 将仓库根加入 `sys.path`，测试无需安装包即可导入 `cue`。

## 约束

- 仅使用标准库 `bisect`、`re` 与 pytest；离线运行，不使用随机数，不调用 sleep。
- CUE 用文本行列表表示；音频用采样点序列表示（`audio_len` 为采样点数），时间轴单位为帧；帧级比较容差为 1 帧。
- 异常统一使用 `cue.splitter.CueError`；所有函数返回新对象，不修改输入。
- `tests/test_splitter.py` 是验收测试，不可修改；只允许修改 `cue/splitter.py`（必要时可调整 `cue/__init__.py` 的导出）。
