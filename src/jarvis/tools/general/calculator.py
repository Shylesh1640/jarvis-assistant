"""Safe arithmetic expression evaluator using AST parsing.

Exposes a single public function ``calculate_expression`` that accepts a
string like ``"2 + 3 * 4"`` and returns the numeric result (or an error
message string if the expression is invalid).
"""

import ast
import operator
import re

from langchain_core.tools import tool

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node: ast.AST):
    """Recursively evaluate an AST node with only safe operations."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value!r}")

    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return op(_eval_node(node.operand))

    if isinstance(node, ast.BinOp):
        op = _ALLOWED_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))

    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


def calculate_expression(expr: str) -> float | str:
    """Evaluate a simple arithmetic expression safely.

    Supports ``+``, ``-``, ``*``, ``/``, ``//``, ``%``, ``**``,
    parentheses, and unary ``+``/``-``.  The caret ``^`` is automatically
    converted to ``**`` (power).

    Returns the numeric result on success, or an error message string on
    failure (e.g. syntax error, division by zero, unsupported construct).
    """
    expr = expr.strip().strip("=").strip()
    if not expr:
        return "Error: empty expression"

    # Normalise common notation: 2^3 -> 2**3
    expr = expr.replace("^", "**")

    try:
        tree = ast.parse(expr, mode="eval")
        result = _eval_node(tree)
        return float(result) if isinstance(result, int) else result
    except (SyntaxError, ValueError, ZeroDivisionError) as exc:
        return f"Error: {exc}"


_MATH_TRIGGERS = [
    "calculate",
    "what is",
    "what's",
    "solve",
    "compute",
    "evaluate",
    "how much is",
]

_BARE_EXPR_RE = re.compile(r"^[\d+\-*/().%^ \t]+$")


def is_math_query(text: str) -> bool:
    """Heuristic check whether *text* is (or contains) a math expression.

    Returns ``True`` if the input starts with a known math trigger phrase
    *or* if, after stripping whitespace, it consists only of digits and
    common math operator characters.
    """
    cleaned = text.strip().lower()

    for trigger in _MATH_TRIGGERS:
        if cleaned.startswith(trigger):
            return True

    no_ws = re.sub(r"\s+", "", text)
    if _BARE_EXPR_RE.match(no_ws) and re.search(r"\d", no_ws):
        return True

    return False


@tool
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression.

    Supports ``+``, ``-``, ``*``, ``/``, ``//``, ``%``, ``**``,
    parentheses, and unary ``+``/``-``.  Use ``^`` for exponentiation.
    Pass the full expression as a single string, e.g. ``"2 + 3 * 4"``.
    """
    return str(calculate_expression(expression))
