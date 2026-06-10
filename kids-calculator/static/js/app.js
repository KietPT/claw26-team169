let expression = "";
let quizState = null;
let busy = false;

const displayEl = document.getElementById("display");
const chatEl = document.getElementById("chat");

function updateDisplay() {
  displayEl.textContent = expression || "0";
}

function addBubble(text, role) {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  chatEl.appendChild(bubble);
  chatEl.scrollTop = chatEl.scrollHeight;
  return bubble;
}

function appendToken(token) {
  if (busy) return;
  if (expression === "0" && /^\d$/.test(token)) {
    expression = token;
  } else {
    expression += token;
  }
  updateDisplay();
}

function clearAll() {
  if (busy) return;
  expression = "";
  updateDisplay();
}

function backspace() {
  if (busy) return;
  expression = expression.slice(0, -1);
  updateDisplay();
}

async function sendMessage(message) {
  if (busy || !message.trim()) return;
  busy = true;

  addBubble(message, "user");
  const loading = addBubble("Đang suy nghĩ...", "bot loading");

  try {
    const res = await fetch("/invocations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, quiz_state: quizState }),
    });

    if (!res.ok) {
      throw new Error(`Lỗi ${res.status}`);
    }

    const data = await res.json();
    quizState = data.quiz_state ?? null;
    loading.remove();
    addBubble(data.response || "Không có phản hồi.", "bot");
  } catch (err) {
    loading.remove();
    addBubble(`Ối! ${err.message}. Thử lại nhé!`, "bot");
  } finally {
    busy = false;
  }
}

async function calculate() {
  if (!expression.trim()) return;
  const msg = expression.trim();
  expression = "";
  updateDisplay();
  await sendMessage(msg);
}

async function explain() {
  if (!expression.trim()) {
    addBubble("Nhập phép tính trước rồi bấm Giải thích nhé!", "bot");
    return;
  }
  const msg = `giải thích ${expression.trim()}`;
  expression = "";
  updateDisplay();
  await sendMessage(msg);
}

async function startQuiz(hard = false) {
  await sendMessage(hard ? "quiz kho" : "quiz");
}

function wireKeypad() {
  document.querySelectorAll("[data-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.key;
      if (key === "C") return clearAll();
      if (key === "⌫") return backspace();
      if (key === "=") return calculate();
      appendToken(key);
    });
  });

  document.getElementById("btn-explain").addEventListener("click", explain);
  document.getElementById("btn-quiz-easy").addEventListener("click", () => startQuiz(false));
  document.getElementById("btn-quiz-hard").addEventListener("click", () => startQuiz(true));
  document.getElementById("btn-help").addEventListener("click", () => sendMessage("help"));
}

updateDisplay();
wireKeypad();
addBubble("Chào bé! 🌟 Bấm số để tính, hoặc chơi Quiz nhé!", "bot");
