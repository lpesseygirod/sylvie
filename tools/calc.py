"""Safe calculator: evaluates arithmetic with Python's AST — no eval(), no names except math ones."""

from __future__ import annotations

import ast
import math
import operator
import re

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
}
_CONSTS = {"pi": math.pi, "e": math.e}


class CalcError(ValueError):
    pass


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 1000:
            raise CalcError("exponent too large")
        return _BIN[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > 1000:
            raise CalcError("number too large")
        return _FUNCS[node.func.id](*args)
    raise CalcError(f"unsupported expression: {ast.dump(node)[:60]}")


def calculate(expression: str) -> str:
    expr = expression.replace("^", "**").replace("×", "*").replace("÷", "/")
    if not re.search(
        r"[a-z_]\s*\(", expr
    ):  # "1,5" is a Spanish/French decimal unless commas separate arguments
        expr = re.sub(r"(\d),(\d)", r"\1.\2", expr)
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expr)  # 17% → (17/100)
    expr = re.sub(r"\bmod\b", "%", expr)
    try:
        result = _eval(ast.parse(expr.strip(), mode="eval"))
    except (SyntaxError, ZeroDivisionError, OverflowError, CalcError, TypeError, ValueError) as e:
        return f"Error: {e}"
    if isinstance(result, float):
        result = round(result, 10)
        if result.is_integer() and abs(result) < 1e15:
            result = int(result)
    return str(result)
