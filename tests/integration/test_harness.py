"""
RPA 集成测试 Harness

提供在测试中运行 RPA 工作流并收集日志/结果的框架。
核心能力:
- 以 daemon 线程运行 ExecutionEngine
- 自动消费 ui_queue 消息（超时自动跳过、暂停自动继续）
- 收集执行日志用于断言
- 支持设置超时保护
"""

import os
import sys
import json
import time
import threading
import traceback
from queue import Queue, Empty
from typing import Any, Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rpa.vision import VisionEngine
from rpa.execution import ExecutionEngine
from rpa.utils import StopException


# ============================================================
# UI Queue Consumer — 在测试线程中消费 ui_queue 消息
# ============================================================

class UIQueueConsumer:
    """
    消费者: 在测试主线程中轮询 ui_queue，自动处理超时/暂停对话框。
    在测试场景中，超时 → 自动"跳过"，暂停 → 自动"继续"。
    """

    def __init__(
        self,
        on_timeout: str = "skip",     # "skip" | "retry"
        on_pause: str = "continue",   # "continue"
    ):
        self.on_timeout = on_timeout
        self.on_pause = on_pause
        self._running = True
        self._logs: List[str] = []

    @property
    def logs(self) -> List[str]:
        return self._logs

    def stop(self) -> None:
        self._running = False

    def consume_once(self, ui_queue: Queue, stop_event: threading.Event,
                     timeout: float = 0.1) -> bool:
        """
        单次消费 ui_queue 中的一条消息（非阻塞轮询）。

        由 IntegrationTestRunner.run() 的主循环反复调用。
        不再包含内部 while 循环，避免阻塞外层 deadline 检查。

        Args:
            ui_queue: 执行引擎的消息队列
            stop_event: 引擎的停止事件
            timeout: 每次 poll 的超时

        Returns:
            True 表示引擎已完成（收到 done/complete/error/stop 且队列已空），
            False 表示引擎仍在运行
        """
        try:
            msg = ui_queue.get(timeout=timeout)
        except Empty:
            # 队列为空，引擎仍在运行中（或已死亡但消息未发）
            return False

        msg_type = msg.get("type", "")

        if msg_type == "log":
            self._logs.append(msg.get("msg", ""))
        elif msg_type == "timeout":
            choice = self.on_timeout
            msg["user_choice"]["action"] = choice
            msg["result_event"].set()
            self._logs.append(f"[AUTO] 超时对话框 → {choice}")
        elif msg_type == "pause":
            choice = self.on_pause
            msg["user_choice"]["continue"] = choice
            msg["result_event"].set()
            self._logs.append(f"[AUTO] 暂停对话框 → {choice}")
        elif msg_type in ("complete", "done"):
            self._logs.append("[INFO] 流程执行完成")
            # 排空队列中剩余消息后报告完成
            _drain_queue(ui_queue, timeout=0.02)
            return True
        elif msg_type == "error":
            self._logs.append(f"[ERROR] {msg.get('msg', '未知错误')}")
            _drain_queue(ui_queue, timeout=0.02)
            return True
        elif msg_type == "stop":
            self._logs.append("[INFO] 流程已停止")
            _drain_queue(ui_queue, timeout=0.02)
            return True
        elif msg_type in ("minimize_gui", "show_gui"):
            # 引擎内部消息，忽略
            pass
        else:
            self._logs.append(f"[UNKNOWN] {msg}")

        return False


# ============================================================
# 集成测试执行器
# ============================================================

class IntegrationTestRunner:
    """
    集成测试执行器。

    用法:
        runner = IntegrationTestRunner(flow_json_path, max_duration=30)
        runner.run()
        assert runner.success
        print(runner.logs)
    """

    def __init__(
        self,
        flow_data: List[Dict[str, Any]],
        max_duration: float = 30.0,
        on_timeout: str = "skip",
        on_pause: str = "continue",
    ):
        """
        Args:
            flow_data: 流程数据 (JSON 解析后的列表)
            max_duration: 单次测试最大执行时长（秒），超时则强制停止
            on_timeout: 超时对话框自动选择 ("skip" | "retry")
            on_pause: 暂停对话框自动选择 ("continue")
        """
        self.flow_data = flow_data
        self.max_duration = max_duration
        self.on_timeout = on_timeout
        self.on_pause = on_pause

        self.ui_queue: Queue = Queue()
        self.stop_event = threading.Event()
        self.vision = VisionEngine()

        self._engine: Optional[ExecutionEngine] = None
        self._consumer: Optional[UIQueueConsumer] = None
        self._consumer_thread: Optional[threading.Thread] = None
        self._success = False
        self._error: Optional[str] = None
        self._logs: List[str] = []

    @property
    def success(self) -> bool:
        return self._success

    @property
    def error(self) -> Optional[str]:
        return self._error

    @property
    def logs(self) -> List[str]:
        return self._logs

    def run(self) -> bool:
        """执行工作流，返回 True 表示成功。"""
        self._logs.clear()
        self._error = None
        self._success = False

        self._engine = ExecutionEngine(
            flow_data=self.flow_data,
            vision=self.vision,
            ui_queue=self.ui_queue,
            stop_event=self.stop_event,
        )

        self._consumer = UIQueueConsumer(
            on_timeout=self.on_timeout,
            on_pause=self.on_pause,
        )

        # 在子线程中启动执行引擎
        self._engine.start()

        # 在主循环中消费 ui_queue 消息
        deadline = time.time() + self.max_duration
        engine_done = False
        try:
            while time.time() < deadline:
                if self.stop_event.is_set():
                    break

                # 单次消费一条消息（非阻塞，每 0.05s 轮询一次）
                finished = self._consumer.consume_once(
                    self.ui_queue, self.stop_event, timeout=0.05)

                if finished:
                    engine_done = True

                # 引擎线程已退出 且 已收到完成信号 → 成功
                if engine_done and not self._engine.is_alive():
                    self._success = True
                    break

                # 引擎线程死了但还没收到完成信号：
                # 可能是 "show_gui" 先于 "done" 被消费导致的竞态
                if not engine_done and not self._engine.is_alive():
                    # 排空队列，检查是否有残留的 done/complete 消息
                    residual_done = _drain_and_check_complete(self.ui_queue)
                    if residual_done:
                        engine_done = True
                        self._success = True
                        self._consumer._logs.append(
                            "[INFO] 流程执行完成（残留消息中检测到完成信号）")
                        break
                    else:
                        self._error = "引擎线程异常退出（未发送完成信号）"
                        break

            else:
                # 超时
                self._error = (
                    f"测试超时 ({self.max_duration}s)，"
                    f"引擎{'已完成' if engine_done else '未完成'}"
                )
                self._force_stop()

        except StopException:
            self._logs.append("[INFO] 流程被停止")
            self._success = True
        except Exception as e:
            self._error = f"执行异常: {e}\n{traceback.format_exc()}"
            self._force_stop()

        self._logs = self._consumer.logs
        return self._success

    def _force_stop(self) -> None:
        """强制停止引擎。"""
        self.stop_event.set()
        if self._engine and self._engine.is_alive():
            self._engine.join(timeout=3)
        _drain_queue(self.ui_queue, timeout=0.05)

    def stop(self) -> None:
        """手动停止。"""
        self._force_stop()


def _drain_queue(q: Queue, timeout: float = 0.05) -> None:
    """排空队列中所有未处理消息。"""
    while True:
        try:
            q.get(timeout=timeout)
        except Empty:
            break


def _drain_and_check_complete(q: Queue, timeout: float = 0.05) -> bool:
    """
    排空队列并检查是否有 done/complete 消息（竞态条件兜底）。

    返回 True 表示在残留消息中找到了完成信号。
    """
    while True:
        try:
            msg = q.get(timeout=timeout)
            if msg.get("type", "") in ("done", "complete"):
                # 继续排空剩余消息
                _drain_queue(q, timeout=0.02)
                return True
        except Empty:
            break
    return False


# ============================================================
# 流程 JSON 构建辅助
# ============================================================

def make_node(node_type: str, node_id: str = None, **params) -> dict:
    """
    快速构建一个流程节点。

    Args:
        node_type: 节点类型 (点击/输入/按键/...)
        node_id: 节点 ID (不提供则自动生成)
        **params: 节点参数

    Returns:
        节点字典
    """
    import uuid
    return {
        "id": node_id or str(uuid.uuid4()),
        "type": node_type,
        "params": params,
    }


def make_click_node(template: str = "", button: str = "左键单击",
                    confidence: str = "0.95", offset_x: str = "0",
                    offset_y: str = "0", use_bg: bool = False,
                    region: str = "", find_nth: str = "1",
                    color_sensitive: bool = True,
                    output_var: str = "") -> dict:
    """构建点击节点。"""
    return make_node("点击",
        template=template,
        button=button,
        confidence=confidence,
        offset_x=offset_x,
        offset_y=offset_y,
        use_bg=use_bg,
        input_region_var=region,
        find_nth=find_nth,
        color_sensitive=color_sensitive,
        color_enable=False,
        target_color="",
        color_tolerance="10",
        output_var=output_var,
    )


def make_input_node(text: str, use_bg: bool = False,
                    input_type: str = "直接输入",
                    pos_var: str = "") -> dict:
    """构建输入节点。

    Args:
        text: 要输入的文本
        use_bg: 是否使用后台模式
        input_type: 输入类型（直接输入 / 数据变量输入）
        pos_var: 点击位置（区域变量统一机制）:
                 - 空字符串 → 中心点击（保底逻辑）
                 - 变量名   → 从运行时变量取值
                 - "x,y"    → 直接坐标
                 - "x1,y1,x2,y2" → 区域（自动取中心）
    """
    return make_node("输入",
        input_type=input_type,
        text=text,
        data_name="数据",
        field_name="",
        pos_var=pos_var,
        use_bg=use_bg,
    )


def make_key_node(key: str, use_bg: bool = False) -> dict:
    """构建按键节点。"""
    return make_node("按键", key=key, use_bg=use_bg)


def make_delay_node(seconds: str = "0.5") -> dict:
    """构建延时节点。"""
    return make_node("延时", seconds=seconds)


def make_window_node(window_title: str,
                     window_action: str = "激活(设为后台目标)") -> dict:
    """构建窗口节点。"""
    return make_node("窗口",
        window_title=window_title,
        window_action=window_action,
    )


def make_label_node(label_name: str) -> dict:
    """构建标签节点。"""
    return make_node("标签", label_name=label_name)


def make_jump_node(target_label: str) -> dict:
    """构建跳转节点。"""
    return make_node("跳转", target_label=target_label)


def make_var_mgr_node(variables: list) -> dict:
    """构建变量管理节点。"""
    return make_node("变量管理", variables=variables)


def make_var_calc_node(expression: str, calc_result_var: str) -> dict:
    """构建变量计算节点。"""
    return make_node("变量计算",
        expression=expression,
        calc_result_var=calc_result_var,
    )


def make_condition_node(var_name: str, op: str, value: str,
                        true_steps: list = None,
                        false_steps: list = None) -> dict:
    """构建条件分支节点。"""
    node = make_node("条件分支",
        condition_type="变量条件",
        var_condition_name=var_name,
        var_condition_op=op,
        var_condition_value=value,
    )
    node["true"] = true_steps or []
    node["false"] = false_steps or []
    return node


def make_data_loop_node(data_file: str, data_name: str = "数据",
                        loop_mode: str = "向下取数",
                        start_index: str = "1",
                        body: list = None) -> dict:
    """构建数据循环节点。"""
    node = make_node("数据循环",
        data_file=data_file,
        data_name=data_name,
        loop_mode=loop_mode,
        start_index=start_index,
    )
    node["body"] = body or []
    return node


def make_scroll_node(direction: str = "向下", clicks: str = "3",
                     use_bg: bool = False) -> dict:
    """构建滚轮节点。"""
    return make_node("滚轮", direction=direction, clicks=clicks, use_bg=use_bg)


def make_pause_node(pause_msg: str = "测试暂停") -> dict:
    """构建暂停节点。"""
    return make_node("暂停", pause_msg=pause_msg)


def make_file_node(file_path: str) -> dict:
    """构建文件节点（打开文件）。"""
    return make_node("文件", file_path=file_path)


def save_workflow(flow: list, filepath: str) -> str:
    """保存流程为 JSON 文件。"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(flow, f, ensure_ascii=False, indent=2)
    return filepath


def load_workflow(filepath: str) -> list:
    """从 JSON 文件加载流程。"""
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)
