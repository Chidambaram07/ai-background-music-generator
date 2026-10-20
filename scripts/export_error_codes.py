import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def error_codes():
    codes = {"NOT_FOUND","METHOD_NOT_ALLOWED","HTTP_ERROR","REQUEST_TOO_LARGE"}
    for path in (ROOT/"backend/app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node,ast.Call):
                continue
            name = node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else ""
            if name in {"AppError","AudioProcessingError","__init__"} and node.args and isinstance(node.args[0],ast.Constant):
                value = node.args[0].value
                if isinstance(value,str) and value.isupper() and value.replace("_","").isalnum():
                    codes.add(value)
            for keyword in node.keywords:
                if keyword.arg == "error_code" and isinstance(keyword.value,ast.Constant):
                    codes.add(keyword.value.value)
    return sorted(codes)


if __name__ == "__main__":
    codes = error_codes()
    path = ROOT/"README.md"
    text = path.read_text(encoding="utf-8")
    start = "<!-- ERROR_CODES_BEGIN -->"
    end = "<!-- ERROR_CODES_END -->"
    block = start+"\n"+", ".join(f"`{code}`" for code in codes)+"\n"+end
    if start in text:
        before,remaining = text.split(start,1)
        text = before+block+remaining.split(end,1)[1]
    else:
        text += "\n## Structured Error Codes\n\n"+block+"\n"
    path.write_text(text,encoding="utf-8")
    print(json.dumps({"documented_error_codes":len(codes)}))