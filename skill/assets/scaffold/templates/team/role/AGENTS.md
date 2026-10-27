# __ROLE_TITLE__

## 身份
你只执行__ROLE_TITLE__的本角色 Skill。不派发成员、不修改团长状态、不创建 child issue。

## 唯一第一动作
每个成员任务，第一个工具调用必须执行本角色 `run.py`。不要先读源码、帮助或历史任务。

```bash
python3 - <<'PY'
import json, runpy, sys
from pathlib import Path

payload = json.loads(Path("context/skills.json").read_text(encoding="utf-8"))

def find(value):
    if isinstance(value, dict):
        if value.get("name") == "__ROLE__":
            return str(value.get("path") or "")
        for item in value.values():
            found = find(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find(item)
            if found:
                return found
    return ""

base = Path(find(payload))
candidates = [
    base / "scripts" / "run.py",
    Path("experts/__ROLE__/skills/__ROLE__/scripts/run.py").resolve(),
]
script = next((path for path in candidates if path.is_file()), None)
if script is None:
    raise SystemExit("__ROLE__ run.py not found")
sys.argv = [
    "run.py",
    "--context", "context/task.json",
    "--context", "context/agent.json",
    "--output", "/tmp/result.json",
]
runpy.run_path(str(script), run_name="__main__")
PY
```

## 输出
写结构化结果和必要 artifact。失败返回结构化 error，保留真实 message。

`run.py` 会把结构化 JSON 写到 `artifacts/member-result.json`，平台随任务完成发布。运行成功后，你给平台的最终回复只写一句中文完成提示，例如“已完成，结果已回传。”；不要重复输出 JSON、命令、路径或 UUID。

## 绝对禁止
继续派发 / 跨角色改写 / 把中间 JSON 当用户回复 / 吞根因 / 空结果。
