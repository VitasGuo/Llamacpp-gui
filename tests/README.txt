"""纯函数冒烟测试（无网络、无 GUI 依赖部分用 offscreen 平台跑）。

运行：.venv\\Scripts\\python.exe -m unittest discover tests
背景：本项目长期零测试，v1.5.0 的"新建脚本对话框 QComboBox.text() 崩溃"
如果有最基本的双击冒烟即可当场拦下（见 traps #9）。
"""
