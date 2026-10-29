"""AstrBot 仓库安装入口，复用 integrations 中的插件实现。"""

from pathlib import Path

_plugin_main = Path(__file__).parent / "integrations" / "astrbot_plugin_anisignal" / "main.py"
# 保持类和命令注册在本入口，同时让相对导入从原插件目录加载。
__path__ = [str(_plugin_main.parent)]
__package__ = __name__
exec(compile(_plugin_main.read_bytes(), str(_plugin_main), "exec"))
