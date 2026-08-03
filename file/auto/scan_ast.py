"""
scan_ast.py
===========
AST Scanner — 掃描專案所有 .py 檔案，提取 class / function / 全域變數資訊
輸出：JSON 格式，供 generate_docs.py 生成 Markdown 文件使用

執行方式（由 update_docs.bat 呼叫，或手動執行）：
    python file/auto/scan_ast.py
"""
import ast
import json
import sys
from pathlib import Path

# ─── 路徑設定（相對於本腳本所在的 file/auto/ 往上兩層即為專案根目錄）─────────
SCRIPT_DIR   = Path(__file__).parent          # .../file/auto/
PROJECT_ROOT = SCRIPT_DIR.parent.parent       # .../專案根目錄/
OUTPUT_JSON  = SCRIPT_DIR / "ast_result.json"

IGNORE_DIRS  = {"__pycache__", ".git", ".vscode", "node_modules", "auto"}


# ─── 輔助函式 ─────────────────────────────────────────────────────────────────

def get_docstring(node):
    """取得 node 的 docstring（若有）"""
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        return node.body[0].value.value.strip()
    return None


def get_default_str(default):
    """將 AST default 轉換成字串"""
    try:
        return ast.unparse(default)
    except Exception:
        return "..."


def extract_function(node):
    """提取 function 或 method 的詳細資訊"""
    args = node.args
    params = []

    # positional args (含 self)
    defaults_offset = len(args.args) - len(args.defaults)
    for i, arg in enumerate(args.args):
        param = {"name": arg.arg}
        if arg.annotation:
            param["type"] = ast.unparse(arg.annotation)
        default_idx = i - defaults_offset
        if default_idx >= 0:
            param["default"] = get_default_str(args.defaults[default_idx])
        params.append(param)

    # *args
    if args.vararg:
        param = {"name": f"*{args.vararg.arg}"}
        if args.vararg.annotation:
            param["type"] = ast.unparse(args.vararg.annotation)
        params.append(param)

    # keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        param = {"name": arg.arg}
        if arg.annotation:
            param["type"] = ast.unparse(arg.annotation)
        if args.kw_defaults[i]:
            param["default"] = get_default_str(args.kw_defaults[i])
        params.append(param)

    # **kwargs
    if args.kwarg:
        param = {"name": f"**{args.kwarg.arg}"}
        if args.kwarg.annotation:
            param["type"] = ast.unparse(args.kwarg.annotation)
        params.append(param)

    return_type = ast.unparse(node.returns) if node.returns else None
    decorators  = [ast.unparse(d) for d in node.decorator_list]

    return {
        "name":        node.name,
        "line":        node.lineno,
        "is_async":    isinstance(node, ast.AsyncFunctionDef),
        "decorators":  decorators,
        "params":      params,
        "return_type": return_type,
        "docstring":   get_docstring(node),
    }


def extract_global_vars(tree):
    """提取模組層的全域變數（Assign + AnnAssign）"""
    variables = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        value_repr = ast.unparse(node.value)
                        if len(value_repr) > 120:
                            value_repr = value_repr[:120] + "..."
                    except Exception:
                        value_repr = "..."
                    variables.append({
                        "name":       target.id,
                        "line":       node.lineno,
                        "value_repr": value_repr,
                    })
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            variables.append({
                "name":       node.target.id,
                "line":       node.lineno,
                "type":       ast.unparse(node.annotation),
                "value_repr": ast.unparse(node.value) if node.value else None,
            })
    return variables


def extract_class(node):
    """提取 class 資訊"""
    bases      = [ast.unparse(b) for b in node.bases]
    methods    = []
    class_vars = []

    for item in ast.iter_child_nodes(node):
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(extract_function(item))
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    try:
                        value_repr = ast.unparse(item.value)
                        if len(value_repr) > 120:
                            value_repr = value_repr[:120] + "..."
                    except Exception:
                        value_repr = "..."
                    class_vars.append({
                        "name":       target.id,
                        "line":       item.lineno,
                        "value_repr": value_repr,
                    })

    return {
        "name":       node.name,
        "line":       node.lineno,
        "bases":      bases,
        "docstring":  get_docstring(node),
        "class_vars": class_vars,
        "methods":    methods,
    }


def scan_file(filepath: Path, root: Path):
    """掃描單一 .py 檔案"""
    source = filepath.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return {"path": str(filepath.relative_to(root)), "error": str(e)}

    result = {
        "path":            str(filepath.relative_to(root)).replace("\\", "/"),
        "module_docstring": get_docstring(tree),
        "global_vars":     extract_global_vars(tree),
        "classes":         [],
        "functions":       [],
        "imports":         [],
    }

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            result["classes"].append(extract_class(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result["functions"].append(extract_function(node))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                result["imports"].append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                result["imports"].append(f"{module}.{alias.name}")

    return result


def scan_project(root: Path) -> list:
    results = []
    for py_file in sorted(root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in py_file.parts):
            continue
        results.append(scan_file(py_file, root))
    return results


# ─── 主程式 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT
    out  = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT_JSON

    print(f"[scan_ast] 掃描目錄：{root}")
    data = scan_project(root)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[scan_ast] 完成：掃描 {len(data)} 個檔案 -> {out}")
