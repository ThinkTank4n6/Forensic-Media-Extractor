"""No engine function refers to a name it does not have.

_run_folder_extraction called vendor_fs.describe(image_path), and
image_path is not one of its names. The NameError was swallowed by the
try around it, so nothing failed and the code never ran. This compiles the
engine and the web layer and lists, for every function, the names it reads
that are neither its own, its module's, nor built in.
"""
import ast
import builtins
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
# media_core and the modules split out of it in v2026.11.314.
FILES = ["fme/engines/media_core.py", "fme/engines/engine_common.py",
         "fme/engines/image_readers.py", "fme/engines/signatures.py",
         "fme/engines/length_parsers.py", "fme/engines/carve_judging.py",
         "fme/engines/embedded.py", "fme/engines/fs_walk.py",
         "fme/engines/deleted_recovery.py", "fme/engines/carving.py",
         "fme/engines/volume_survey.py", "fme/engines/sources.py",
         "fme/engines/fat32_core.py",
         "fme/engines/ntfs_mft.py", "fme/web/media_gui.py",
         "fme/web/request_guard.py", "fme/exports/report.py"]


def unknown_names(path: str) -> list:
    tree = ast.parse(open(os.path.join(PROJECT, path), encoding="utf-8").read())
    module = set(dir(builtins)) | {"__file__", "__name__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                module.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.Global):
            module.update(node.names)
    for node in tree.body:
        for target in ast.walk(node) if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Try, ast.If, ast.For, ast.With)) else []:
            if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store):
                module.add(target.id)
    found = []

    def own_names(func) -> set:
        """Names a function binds itself, not counting nested functions' own."""
        names = set()
        for arg in ast.walk(func.args):
            if isinstance(arg, ast.arg):
                names.add(arg.arg)
        stack = list(ast.iter_child_nodes(func))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                continue                       # its body is its own scope
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names.add(node.id)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.Nonlocal, ast.Global)):
                names.update(node.names)
            elif isinstance(node, ast.comprehension):
                for target in ast.walk(node.target):
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, ast.NamedExpr):
                names.add(node.target.id)
            stack.extend(ast.iter_child_nodes(node))
        return names

    def reads(func) -> list:
        """Names a function reads in its own body (lambdas and comprehensions included)."""
        out = []
        stack = list(ast.iter_child_nodes(func))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stack.extend(node.decorator_list)
                continue
            if isinstance(node, ast.Lambda):
                for arg in ast.walk(node.args):
                    if isinstance(arg, ast.arg):
                        lambda_args.add(arg.arg)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                out.append(node)
            stack.extend(ast.iter_child_nodes(node))
        return out

    lambda_args = set()

    def visit(node, outer):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = outer | own_names(child)
                for name in reads(child):
                    if name.id not in scope and name.id not in module \
                            and name.id not in lambda_args:
                        found.append(f"{path}:{name.lineno} {child.name}() reads {name.id}")
                visit(child, scope)
            elif isinstance(child, ast.ClassDef):
                visit(child, outer)
            else:
                visit(child, outer)

    visit(tree, set())
    return found


problems = [p for path in FILES for p in unknown_names(path)]
for line in problems:
    print("  " + line)
print(f"names read that no scope defines: {len(problems)}")
print("\nALL CHECKS PASS" if not problems else "\nFAILURES ABOVE")
sys.exit(0 if not problems else 1)
