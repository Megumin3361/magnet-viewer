"""libtorrent 引擎抽象层：会话/句柄语义操作的最小接口（ABC + 数据类）。

本模块只定义接口与值对象，不含任何 libtorrent 依赖；具体实现见
``core.engine_lt``（python-libtorrent 2.1.x 绑定）。``core.fetcher`` 后续
接线（t6 规划）将依赖本层而非直接 import libtorrent——当前任务只交付
抽象与一致性测试（engine_conformance_test.py），不改写 fetcher。

设计原则（与 plan/t2 的「接线期零行为变化」一致）：

- **一个语义操作一个方法**，而不是一个 libtorrent 调用一个方法；
- 接口是「还原现状」的：只收录 fetcher.py / scheduler.py 今天真实调用
  的操作，不发明未来才需要的 API；
- 掩码、settings 键、flags 组装等全部留在实现层（LibtorrentEngine），
  fetcher 侧不再感知 ``lt.*`` 符号；
- 错误策略沿用现状：实现层不吞异常（由调用方按原 try/except 处理），
  语义与直接调用 libtorrent 完全一致。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EngineParams:
    """add_torrent 的参数载体（对应 libtorrent add_torrent_params）。

    flag_bits 为引擎无关的位图：实现层负责与自身 torrent_flags 换算。
    upload_mode / auto_managed / seed_mode 是全部调用点用到的三个语义位。
    """

    info_hash: str = ""                    # 未 add 前即可知的 info_hash（磁力链 btih）
    save_path: str = ""                    # 落盘目录（绝对路径）
    url: str = ""                          # 磁力链 URI（restore 注入 resume data 时用）
    trackers: list[str] = field(default_factory=list)
    flag_bits: int = 0                     # 引擎无关 flags 位图（见 FLAG_* 常量）
    torrent_info_bytes: bytes = b""        # 完整 .torrent 文件 bencode（非空 = 本地种子加入）


# EngineParams.flag_bits 的语义位（引擎无关；LibtorrentEngine 负责映射到
# libtorrent torrent_flags / 自定义位）。
FLAG_UPLOAD_MODE = 1       # 只上传/解析，不下载资源
FLAG_AUTO_MANAGED = 2      # 由 libtorrent 队列管理（active_downloads 调度）
FLAG_SEED_MODE = 4         # 数据已完整，跳过校验直接做种

FLAG_NAME_TO_BIT = {
    "upload_mode": FLAG_UPLOAD_MODE,
    "auto_managed": FLAG_AUTO_MANAGED,
    "seed_mode": FLAG_SEED_MODE,
}


@dataclass
class EngineStatus:
    """任务状态快照（对齐 fetcher.status() / tasks() 实际读取的字段）。"""

    total_done: int = 0                    # 已完成字节
    download_payload_rate: int = 0         # 下载速率（B/s）
    num_peers: int = 0
    num_seeds: int = 0
    state: int = -1                        # 引擎原始状态枚举值（实现层释义）
    progress: float = 0.0                  # 0~1
    priority: int = 0                      # 队列优先级（0~255，引擎语义）
    pieces: list = field(default_factory=list)  # 分块位图（可按索引取真值）
    has_metadata: bool = False             # 元数据是否就绪


@dataclass
class EngineAlert:
    """引擎告警的归一化载体（元数据就绪 / 文件完成 / resume 数据）。"""

    kind: str = ""                         # "metadata" | "file_completed" | "resume" | ...
    info_hash: str = ""                    # 告警归属（已知时）
    index: int = -1                        # file_completed_alert：完成的文件序号
    resume_data: dict | None = None        # save_resume_data_alert：resume 字典


class EngineHandle(ABC):
    """单个任务的句柄抽象（对应 libtorrent torrent_handle）。

    均为薄转发：方法体与 fetcher.py 今天对 torrent_handle 的调用方式
    保持一致（同参数、同返回形态），保证接线期零行为变化。
    """

    @abstractmethod
    def status(self) -> EngineStatus:
        """状态快照（一次调用即取全部批量字段，含 pieces 位图）。"""

    @abstractmethod
    def pause(self) -> None:
        """暂停任务。"""

    @abstractmethod
    def resume(self) -> None:
        """恢复任务。"""

    @abstractmethod
    def set_priority(self, priority: int) -> None:
        """队列优先级（0~255；libtorrent 2.x 正确 API，见 fetcher 注释）。"""

    @abstractmethod
    def prioritize_files(self, priorities: list[int]) -> None:
        """按文件序号设置下载优先级（0 = 不下载）。"""

    @abstractmethod
    def save_resume_data(self, flush: bool = True) -> None:
        """请求写 resume data（异步；告警回到 pop_alerts）。"""

    @abstractmethod
    def need_save_resume_data(self) -> bool:
        """resume data 是否有未落盘的变更。"""

    @abstractmethod
    def file_progress(self) -> list[int]:
        """每文件已下载字节数（顺序对齐 torrent_file 文件序号）。"""

    @abstractmethod
    def torrent_metadata(self):
        """元数据（bytes info 区段）；未就绪返回 None。"""

    @abstractmethod
    def num_files(self) -> int:
        """文件总数（元数据未就绪返回 0）。"""

    @abstractmethod
    def piece_length(self) -> int:
        """分块大小（元数据未就绪返回 0）。"""

    @abstractmethod
    def set_piece_deadline(self, piece: int, deadline: int = 0) -> None:
        """把某分块置为 ASAP 截止（预览调度点播）。"""

    @abstractmethod
    def clear_piece_deadlines(self) -> None:
        """清空全部 deadline（seek/停预览前撤旧预约）。"""

    @abstractmethod
    def have_piece(self, piece: int) -> bool:
        """指定分块是否已完整落盘。"""

    @abstractmethod
    def connect_peer(self, ip: str, port: int) -> None:
        """手动添加 Peer（跳过 DHT 发现；整数对由实现层组装）。"""

    @abstractmethod
    def get_flags(self) -> int:
        """当前任务 flags 位图（EngineParams.flag_bits 同一编码）。"""

    @abstractmethod
    def set_flags(self, bits: int) -> None:
        """按位加 flags（等价 libtorrent set_flags（多个语义位））。"""

    @abstractmethod
    def clear_flags(self, bits: int) -> None:
        """按位清 flags（等价 unset_flags）。"""

    @abstractmethod
    def info_hash(self) -> str:
        """info_hash 十六进制小写（40/64 位；无效返回空串）。"""


class TorrentEngine(ABC):
    """libtorrent 会话语义抽象（SessionManager 所需最小操作集）。"""

    @abstractmethod
    def create_session(self, settings: dict) -> None:
        """创建会话（失败抛异常，由调用方决定是否回退随机端口）。"""

    @abstractmethod
    def apply_session_settings(self, settings: dict) -> None:
        """热更新会话设置（代理/限速开关等）。"""

    @abstractmethod
    def get_session_settings(self) -> dict:
        """导出全部会话设置（dict，键为 libtorrent 字符串名）。"""

    @abstractmethod
    def alert_category_mask(self, names: list[str]) -> int:
        """按类别名拼 alert mask（未知名跳过，不抛错——沿用 start() 语义）。"""

    @abstractmethod
    def parse_magnet(self, uri: str) -> EngineParams:
        """磁力链 → EngineParams（解析失败抛异常，调用方沿用原错误文案）。"""

    @abstractmethod
    def make_torrent_params(self, torrent_file_path: str) -> EngineParams:
        """本地 .torrent 文件 → EngineParams（含 torrent 元数据）。"""

    @abstractmethod
    def params_from_resume(self, resume_bytes: bytes,
                           torrent_file_path: str | None = None) -> EngineParams | None:
        """fastresume 字节 → params（损坏返回 None，调用方静默全新加入）。"""

    @abstractmethod
    def add_torrent(self, params: EngineParams) -> EngineHandle:
        """加入会话（返回句柄；失败抛异常）。"""

    @abstractmethod
    def remove_torrent(self, handle: EngineHandle, delete_files: bool = False) -> None:
        """移除任务（delete_files=True 时引擎层删文件）。"""

    @abstractmethod
    def pop_alerts(self) -> list:
        """取走当前告警批次（元素为 EngineAlert）。"""

    @abstractmethod
    def shutdown(self) -> None:
        """析构会话（drop 引用即生效，与现状语义一致）。"""
