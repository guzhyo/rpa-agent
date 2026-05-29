# RPA 集成测试 — Excel 真实环境测试

## 概述

本目录包含以 **Microsoft Excel** 为目标应用的真实环境集成测试，验证 RPA 自动化工具的各类节点在实际 Windows 环境下的正确性。

与 `tests/test_all_nodes.py`（纯逻辑单元测试）不同，集成测试：
- 真实启动 Excel 进程
- 真实向 Excel 窗口发送 Win32 消息
- 真实读取 Excel 文件内容进行断言

## 目录结构

```
tests/integration/
├── __init__.py                    # 包初始化
├── excel_helper.py                # Excel 辅助模块（创建/读取/进程管理）
├── test_harness.py                # 测试框架（ExecutionEngine 运行器、流程构建器）
├── test_excel_integration.py      # 主测试文件（11个测试场景）
├── test_data/                     # 测试数据目录（Excel/CSV/模板图片）
└── test_workflows/                # 工作流 JSON 文件目录
```

## 前置条件

### 1. 安装依赖
```bash
pip install openpyxl pytest
```

### 2. 安装 Microsoft Excel
- 需安装 Microsoft Excel（Office 2016 或更高版本）
- 默认搜索路径：
  - `C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE`
  - `C:\Program Files (x86)\Microsoft Office\root\Office16\EXCEL.EXE`

### 3. 关闭已打开的 Excel
建议运行测试前关闭所有 Excel 窗口，避免窗口标题冲突。

## 测试场景总览

| 场景 | 测试类 | 覆盖节点 | 说明 |
|------|--------|----------|------|
| 1 | `TestScenario01_WindowAndInput` | 窗口、输入 | 激活 Excel → 后台输入文本 |
| 2 | `TestScenario02_KeyCombinations` | 按键 | Ctrl+A/C/V + Tab 组合键 |
| 3 | `TestScenario03_Delay` | 延时 | 验证延时实际生效 |
| 4 | `TestScenario04_Variables` | 变量管理、变量计算 | 注册变量 + 表达式计算 |
| 5 | `TestScenario05_ConditionalBranch` | 条件分支 | 变量条件 True/False 分支 |
| 6 | `TestScenario06_DataLoop` | 数据循环 | CSV 读取 → Excel 逐行填入 |
| 7 | `TestScenario07_LabelAndJump` | 标签、跳转 | 跳转跳过中间步骤 |
| 8 | `TestScenario08_Scroll` | 滚轮 | 后台滚轮消息 |
| 9 | `TestScenario09_Pause` | 暂停 | 暂停节点自动恢复 |
| 10 | `TestScenario10_FileNode` | 文件 | 通过文件节点打开 Excel |
| 11 | `TestScenario11_EndToEnd` | 综合 | 多节点组合端到端流程 |

## 运行测试

### 运行所有集成测试
```bash
python -m pytest tests/integration/test_excel_integration.py -v -s
```

### 运行单个场景
```bash
python -m pytest tests/integration/test_excel_integration.py::TestScenario01_WindowAndInput -v -s
```

### 直接运行（非 pytest）
```bash
python tests/integration/test_excel_integration.py
```

### 运行并生成报告
```bash
python -m pytest tests/integration/test_excel_integration.py -v --tb=short --durations=10
```

## 测试框架设计

### 核心组件

**`IntegrationTestRunner`** (`test_harness.py`):
- 封装 `ExecutionEngine` 的启动和生命周期管理
- 自动消费 `ui_queue` 消息（超时→跳过，暂停→继续）
- 收集执行日志用于断言
- 超时保护机制

**`UIQueueConsumer`** (`test_harness.py`):
- 在测试主线程中轮询消费 `ui_queue`
- 自动响应用户交互对话框

**`excel_helper.py`**:
- `create_test_excel()` — 用 openpyxl 创建测试数据
- `launch_excel()` / `kill_excel()` — 管理 Excel 进程
- `read_excel_cell()` — 读取单元格用于断言
- `find_excel_exe()` — 自动查找 Excel 路径

### 流程图

```
测试用例 setUp()
  ├─ create_test_excel()   → 生成测试 .xlsx 文件
  └─ launch_excel()        → 启动 Excel 进程

测试用例 test_xxx()
  ├─ make_xxx_node()       → 构建工作流 JSON
  ├─ IntegrationTestRunner.run() → 执行工作流
  │   ├─ ExecutionEngine (daemon线程)
  │   └─ UIQueueConsumer (主线程消费)
  └─ assert 验证           → 读取 Excel / 检查日志

测试用例 tearDown()
  ├─ kill_excel()          → 关闭 Excel
  └─ os.unlink()           → 清理临时文件
```

## 关键设计决策

### 为什么使用后台模式？
- 测试尽可能使用 `use_bg=True`（后台 Win32 消息），避免干扰用户屏幕
- 后台模式通过 `PostMessageW` 发送 Windows 消息，不依赖鼠标键盘模拟

### 对话框自动处理
- 真实环境的窗口查找可能偶发失败，测试 harness 自动将：
  - **超时对话框** → "跳过"
  - **暂停对话框** → "继续"
- 这使测试不会因偶发问题而卡住

### 每个测试独立
- 每个测试用例创建独立的临时 Excel 文件
- 测试结束后立即关闭 Excel 并清理文件
- 避免用例之间相互干扰

## 扩展指南

### 添加新测试场景

1. 在 `test_excel_integration.py` 中继承 `BaseExcelIntegrationTest`
2. 覆盖 `_create_test_file()` 准备测试数据
3. 使用 `make_*_node()` 构建工作流
4. 用 `self._run_workflow()` 执行
5. 用 `self._assert_cell_value()` 或 `self._assert_contains_log()` 验证

```python
class TestScenario12_MyNewTest(BaseExcelIntegrationTest):
    def test_my_feature(self):
        flow = [
            make_window_node(self._get_filename(), "激活(设为后台目标)"),
            make_input_node("测试数据", use_bg=True),
        ]
        runner = self._run_workflow(flow)
        self.assertTrue(runner.success)
        self._assert_cell_value("A1", "测试数据")
```

### 添加模板匹配测试（需要截图）

如需测试图像识别节点，需要：
1. 手动截取 Excel 界面元素的截图，放入 `test_data/` 目录
2. 使用 `make_click_node(template="xxx.png")` 构建点击节点
3. 确保截图中的界面元素在测试运行时可见

## 注意事项

1. **Excel 版本兼容性**: 不同 Excel 版本窗口标题格式可能不同，如 `- Excel` vs `- Microsoft Excel`
2. **后台输入限制**: 后台 `WM_CHAR` 消息在中文输入时可能不生效（仅支持 ASCII 字符）
3. **Excel 就绪时间**: 启动 Excel 后需要等待窗口完全就绪（默认等待 1 秒）
4. **文件锁**: 测试结束后确保关闭 Excel 进程，否则 openpyxl 读取文件会报文件占用
