"""Kids Calculator — friendly math agent for children (Vietnamese)."""

from __future__ import annotations

import ast
import operator
import random
import re
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from greennode_agentbase import GreenNodeAgentBaseApp, PingStatus, RequestContext
from starlette.requests import Request
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles

load_dotenv()

app = GreenNodeAgentBaseApp()
STATIC_DIR = Path(__file__).parent / "static"

ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

OP_SYMBOLS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "×",
    ast.Div: "÷",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "^",
}

VIET_OP_WORDS = {
    "cong": "+",
    "cộng": "+",
    "tru": "-",
    "trừ": "-",
    "nhan": "*",
    "nhân": "*",
    "chia": "/",
    "mu": "**",
    "mũ": "**",
}

QUIZ_LEVELS = {
    "de": {"label": "dễ", "max": 10, "ops": ["+", "-"]},
    "easy": {"label": "dễ", "max": 10, "ops": ["+", "-"]},
    "kho": {"label": "khó", "max": 50, "ops": ["+", "-", "*"]},
    "hard": {"label": "khó", "max": 50, "ops": ["+", "-", "*"]},
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in ALLOWED_UNARYOPS:
        return ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_BINOPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Div) and right == 0:
            raise ZeroDivisionError("Không chia được cho 0 nhé!")
        if isinstance(node.op, ast.FloorDiv) and right == 0:
            raise ZeroDivisionError("Không chia được cho 0 nhé!")
        if isinstance(node.op, ast.Mod) and right == 0:
            raise ZeroDivisionError("Không chia được cho 0 nhé!")
        return ALLOWED_BINOPS[type(node.op)](left, right)
    raise ValueError("Chỉ làm được phép tính với số và + - × ÷ thôi nhé!")


def safe_calculate(expression: str) -> float:
    expr = expression.strip().replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r"\s+", "", expr)
    if not expr:
        raise ValueError("Bạn chưa nhập phép tính nào cả!")
    if not re.fullmatch(r"[\d.+\-*/()%]+", expr):
        raise ValueError("Mình chỉ hiểu số và các phép + - × ÷ ( ) thôi nhé!")

    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree.body)


def normalize_vietnamese_math(text: str) -> str:
    lowered = text.lower().strip()
    for word, symbol in VIET_OP_WORDS.items():
        lowered = lowered.replace(word, f" {symbol} ")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    # "2 + 3" from "2 cộng 3"
    return lowered


def explain_add(a: float, b: float) -> str:
    return (
        f"🍎 Cộng nghĩa là gom lại:\n"
        f"   {int(a) if a == int(a) else a} + {int(b) if b == int(b) else b}\n"
        f"   = {int(a + b) if (a + b) == int(a + b) else a + b}"
    )


def explain_sub(a: float, b: float) -> str:
    return (
        f"🐸 Trừ nghĩa là bớt đi:\n"
        f"   {int(a) if a == int(a) else a} - {int(b) if b == int(b) else b}\n"
        f"   = {int(a - b) if (a - b) == int(a - b) else a - b}"
    )


def explain_mul(a: float, b: float) -> str:
    a_i, b_i = int(a), int(b)
    if a == a_i and b == b_i and 0 <= a_i <= 12 and 0 <= b_i <= 12:
        lines = [f"⭐ {a_i} × {b_i} = tổng của {b_i} số {a_i}:"]
        for i in range(1, b_i + 1):
            partial = a_i * i
            lines.append(f"   {a_i} + ... + {a_i} ({i} lần) = {partial}")
        lines.append(f"   Kết quả: {a_i * b_i}")
        return "\n".join(lines)
    return f"⭐ Nhân là lấy {a} lặp lại {b} lần → kết quả = {a * b}"


def explain_div(a: float, b: float) -> str:
    if b == 0:
        raise ZeroDivisionError("Không chia được cho 0 nhé!")
    return (
        f"🍰 Chia {a} thành {b} phần bằng nhau:\n"
        f"   {a} ÷ {b} = {a / b}"
    )


def explain_expression(expression: str) -> str:
    expr = normalize_vietnamese_math(expression)
    expr = expr.replace("×", "*").replace("÷", "/")
    simple = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)", expr)
    if simple:
        a, op, b = float(simple.group(1)), simple.group(2), float(simple.group(3))
        if op == "+":
            return explain_add(a, b)
        if op == "-":
            return explain_sub(a, b)
        if op == "*":
            return explain_mul(a, b)
        if op == "/":
            return explain_div(a, b)

    result = safe_calculate(expr)
    display = expression.strip()
    if result == int(result):
        return f"📝 {display}\n   = {int(result)}"
    return f"📝 {display}\n   = {result}"


def generate_quiz(level: str = "de") -> dict:
    cfg = QUIZ_LEVELS.get(level.lower(), QUIZ_LEVELS["de"])
    symbols = {"+": "+", "-": "-", "*": "×", "/": "÷"}
    for _ in range(30):
        op = random.choice(cfg["ops"])
        a = random.randint(1, cfg["max"])
        b = random.randint(1, cfg["max"])
        if op == "-" and b > a:
            a, b = b, a
        if op == "-" and b == a:
            continue
        if op == "*" and (a == 1 or b == 1):
            continue
        break
    else:
        op, a, b = "+", 3, 5
    question = f"{a} {symbols[op]} {b}"
    answer = safe_calculate(question.replace("×", "*").replace("÷", "/"))
    return {
        "question": question,
        "answer": int(answer) if answer == int(answer) else answer,
        "level": cfg["label"],
        "hint": _quiz_hint(op, a, b),
    }


def _quiz_hint(op: str, a: float, b: float) -> str:
    if op == "+":
        return f"Gợi ý: đếm thêm {int(b)} từ số {int(a)}"
    if op == "-":
        return f"Gợi ý: từ {int(a)} bớt đi {int(b)}"
    if op == "*":
        return f"Gợi ý: {int(a)} + {int(a)} + ... ({int(b)} lần)"
    return "Gợi ý: chia đều ra nhé!"


def _strip_command_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    lowered = text.lower()
    for prefix in sorted(prefixes, key=len, reverse=True):
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    return text.strip()


def help_message() -> str:
    return (
        "🧮 Xin chào! Mình là máy tính toán cho bé!\n\n"
        "Bạn có thể:\n"
        "• Gõ phép tính: `12 + 8`, `5 × 7`, `20 ÷ 4`\n"
        "• Gõ tiếng Việt: `3 cộng 5`, `10 trừ 4`\n"
        "• Gõ `giải thích 6 + 4` để xem cách làm\n"
        "• Gõ `quiz` hoặc `câu hỏi` để chơi đố vui\n"
        "• Gõ `quiz kho` cho câu khó hơn\n"
        "• Gõ `kiểm tra 15` khi đang chơi quiz (kèm câu hỏi trong tin nhắn trước)\n\n"
        "Chúc bé học toán vui vẻ! 🌟"
    )


def handle_message(message: str, quiz_state: dict | None) -> tuple[str, dict | None]:
    text = (message or "").strip()
    if not text:
        return "Bạn muốn tính gì nào? Gõ `help` để xem hướng dẫn nhé! 😊", quiz_state

    lowered = text.lower()

    if lowered in {"help", "giup", "giúp", "huong dan", "hướng dẫn", "?"}:
        return help_message(), quiz_state

    if lowered.startswith("quiz") or lowered in {"cau hoi", "câu hỏi", "choi", "chơi"}:
        level = "de"
        for key in ("kho", "hard", "de", "easy"):
            if key in lowered:
                level = key
                break
        quiz = generate_quiz(level)
        new_state = {"pending": quiz}
        return (
            f"🎯 Câu hỏi ({quiz['level']}): {quiz['question']} = ?\n"
            f"💡 {quiz['hint']}\n"
            "Trả lời bằng số nhé! (ví dụ: `7`)",
            new_state,
        )

    if lowered.startswith("giải thích") or lowered.startswith("giai thich"):
        expr = _strip_command_prefix(text, ("giải thích", "giai thich"))
        if not expr:
            return "Bạn muốn mình giải thích phép tính nào? Ví dụ: `giải thích 5 + 3`", quiz_state
        try:
            return explain_expression(expr), quiz_state
        except (ValueError, ZeroDivisionError, SyntaxError) as exc:
            return f"😅 {exc}", quiz_state

    if lowered.startswith("kiểm tra") or lowered.startswith("kiem tra"):
        answer_text = _strip_command_prefix(text, ("kiểm tra", "kiem tra"))
        if not answer_text:
            return "Gõ đáp án số thôi nhé! Ví dụ: `15`", quiz_state
        try:
            user_answer = safe_calculate(answer_text)
        except (ValueError, ZeroDivisionError, SyntaxError):
            return "Đáp án phải là một số nhé!", quiz_state
        if quiz_state and "pending" in quiz_state:
            correct = quiz_state["pending"]["answer"]
            if float(user_answer) == float(correct):
                return (
                    f"🎉 Đúng rồi! {quiz_state['pending']['question']} = {correct}\n"
                    "Giỏi lắm! Gõ `quiz` để chơi tiếp nhé!",
                    None,
                )
            return (
                f"🤔 Chưa đúng rồi. Đáp án đúng là {correct}.\n"
                f"💡 {quiz_state['pending']['hint']}\n"
                "Thử lại hoặc gõ `quiz` câu mới nhé!",
                quiz_state,
            )
        return "Chưa có câu quiz nào. Gõ `quiz` để bắt đầu!", quiz_state

    # Plain numeric answer while quiz is active
    if quiz_state and "pending" in quiz_state and re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return handle_message(f"kiểm tra {text}", quiz_state)

    # Calculate expression
    try:
        expr = normalize_vietnamese_math(text)
        result = safe_calculate(expr)
        if result == int(result):
            result_str = str(int(result))
        else:
            result_str = str(round(result, 6)).rstrip("0").rstrip(".")
        return f"✨ {text}\n   = {result_str}\n\nGõ `giải thích {text}` nếu muốn xem cách làm nhé!", quiz_state
    except (ValueError, ZeroDivisionError, SyntaxError) as exc:
        return (
            f"😅 {exc}\n\n"
            "Thử lại nhé! Ví dụ: `2 + 2`, `10 - 3`, hoặc gõ `help`",
            quiz_state,
        )


@app.entrypoint
def handler(payload: dict, context: RequestContext) -> dict:
    message = payload.get("message", "")
    quiz_state = payload.get("quiz_state")

    response, new_quiz_state = handle_message(message, quiz_state)

    return {
        "status": "success",
        "response": response,
        "quiz_state": new_quiz_state,
        "timestamp": datetime.now().isoformat(),
        "session_id": context.session_id,
    }


@app.ping
def health_check() -> PingStatus:
    return PingStatus.HEALTHY


async def index(_: Request) -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if STATIC_DIR.is_dir():
    app.add_route("/", index, methods=["GET"])
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    app.run(port=8080, host="0.0.0.0")
