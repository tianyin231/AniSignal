"""AstrBot 仓库安装入口，复用 integrations 中的插件实现。"""

from pathlib import Path

_plugin_main = Path(__file__).parent / "integrations" / "astrbot_plugin_anisignal" / "main.py"
# 插件类和命令注册在本模块名下（AstrBot 按它查找插件），相对导入改从 integrations 目录加载。
# 本模块不能设置 __path__：AstrBot 以 __import__(name, fromlist=["main"]) 导入入口，
# 入口若是包，会把插件实现当作子模块 main.main 再执行一遍，注册出没有实例的重复命令和工具。
_parent = __name__.rpartition(".")[0]
__package__ = ".".join(filter(None, (_parent, "integrations", "astrbot_plugin_anisignal")))
exec(compile(_plugin_main.read_bytes(), str(_plugin_main), "exec"))
