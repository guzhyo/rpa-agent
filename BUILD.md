# 打包指南

## 方法一：本地打包（推荐开发测试）

### 前提条件
- Windows 10/11
- Python 3.8 或更高版本
- 安装所有依赖：`pip install -r requirements.txt`

### 打包步骤

1. **双击运行打包脚本**
   ```
   build.bat
   ```

2. **或手动执行命令**
   ```bash
   pip install pyinstaller
   pyinstaller rpa_tool.spec --clean
   ```

3. **查看输出**
   - 可执行文件位置：`dist/RPA自动化工具.exe`

## 方法二：GitHub Actions 自动打包

### 触发方式

1. **推送到 main 分支**
   - 每次推送代码到 main 分支会自动触发打包

2. **推送 tag**
   ```bash
   git tag v0.8.1
   git push origin v0.8.1
   ```

3. **手动触发**
   - 进入 GitHub 仓库 → Actions → Build Windows Executable
   - 点击 "Run workflow" 按钮

### 下载构建产物

1. 进入 Actions 页面
2. 点击最新的工作流运行记录
3. 在 "Artifacts" 部分下载 `RPA自动化工具-Windows`

## 打包配置说明

### rpa_tool.spec
- 单文件模式（`--onefile` 效果）
- 无控制台窗口（GUI 应用）
- UPX 压缩减小体积
- 包含所有隐藏依赖

### 排除的模块
为减小体积，以下模块被排除：
- matplotlib
- scipy
- pytest
- unittest
- pdb
- email/http/xml/html

## 常见问题

### 1. 打包后无法运行
- 检查是否缺少 DLL 文件
- 尝试在 `hiddenimports` 中添加缺失的模块

### 2. 文件体积过大
- UPX 压缩已启用
- 检查 `excludes` 列表是否包含不必要的模块

### 3. 杀毒软件误报
- PyInstaller 打包的程序可能被某些杀毒软件误报
- 可以添加信任或购买代码签名证书

## 发布到 GitHub Releases

推送 tag 时会自动创建 Release：

```bash
git tag v0.8.1
git push origin v0.8.1
```

Release 页面会自动包含打包好的 exe 文件。
