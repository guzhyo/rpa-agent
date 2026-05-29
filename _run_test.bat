@echo off
chcp 65001 >nul
cd /d "d:\C盘瘦身搬家目录(勿删改)\PythonProjects\rpa-automation-tool"
.venv\Scripts\python.exe -m pytest tests/integration/test_excel_integration.py::TestScenario01_WindowAndInput tests/integration/test_excel_integration.py::TestScenario05_ConditionalBranch::test_condition_true_branch tests/integration/test_excel_integration.py::TestScenario05_ConditionalBranch::test_condition_false_branch -v --tb=short -s > test_fix_output.txt 2>&1
echo Exit code: %ERRORLEVEL% >> test_fix_output.txt
type test_fix_output.txt
