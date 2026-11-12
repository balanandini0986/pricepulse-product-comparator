(function () {
  const chatHistory = [];
  let isSending = false;

  const fab = document.getElementById("chat-fab");
  const widget = document.getElementById("chat-widget");
  const closeBtn = document.getElementById("chat-close-btn");
  const clearBtn = document.getElementById("chat-clear-btn");
  const msgBox = document.getElementById("chat-messages");
  const sugBox = document.getElementById("chat-suggestions");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");

  function renderMarkdownSafe(text) {
    const escaped = escapeHtml(text || "");
    return escaped
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\n/g, "<br>");
  }

  function appendMessage(role, text, extras) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;
    bubble.innerHTML = role === "bot" ? renderMarkdownSafe(text) : escapeHtml(text);

    if (extras && Array.isArray(extras.suggested_items) && extras.suggested_items.length) {
      const wrap = document.createElement("div");
      wrap.className = "chat-item-cards";
      extras.suggested_items.forEach((it) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chat-mini-item";
        btn.innerHTML = `<strong>${escapeHtml(it.platform)}</strong> • ${escapeHtml(it.title)} — <span style="color:#34d399;font-weight:800;">${formatInr(it.price_inr)}</span> →`;
        btn.addEventListener("click", () => {
          if (typeof openComparison === "function") {
            openComparison(it, false);
          }
        });
        wrap.appendChild(btn);
      });
      bubble.appendChild(wrap);
    }

    msgBox.appendChild(bubble);
    msgBox.scrollTop = msgBox.scrollHeight;
    return bubble;
  }

  function renderSuggestions(list) {
    sugBox.innerHTML = "";
    (list || []).forEach((s) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-chip";
      btn.textContent = s;
      btn.addEventListener("click", () => sendChatMessage(s));
      sugBox.appendChild(btn);
    });
  }

  function showWelcome() {
    msgBox.innerHTML = "";
    appendMessage(
      "bot",
      "Hi! I'm **PricePulse AI Support**. I can help you compare exact products live, explain verified buyer reviews & delivery terms, or troubleshoot any store issue."
    );
    renderSuggestions([
      "Which platform should I buy from?",
      "Compare boAt Airdopes 141",
      "Why is Amazon not showing?",
    ]);
  }

  async function sendChatMessage(rawText) {
    const text = (rawText || "").trim();
    if (!text || isSending) return;
    isSending = true;

    appendMessage("user", text);
    input.value = "";
    const typingEl = appendMessage("bot", "Checking live data...");

    try {
      const priorityEl = document.getElementById("priority-select");
      const compareVisible = !document.getElementById("compare-view").classList.contains("hidden");
      const payload = {
        message: text,
        history: chatHistory.slice(-8),
        context: {
          priority: priorityEl ? priorityEl.value : "balanced",
          compareData: compareVisible && typeof state !== "undefined" ? state.compareData : null,
          selectedItem: compareVisible && typeof state !== "undefined" ? state.selectedItem : null,
        },
      };

      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      typingEl.remove();

      const replyText = data.reply || data.error || "Sorry, I couldn't process that right now.";
      appendMessage("bot", replyText, { suggested_items: data.suggested_items });
      renderSuggestions(data.suggestions);

      chatHistory.push({ role: "user", text });
      chatHistory.push({
        role: "assistant",
        text: replyText,
        compareData: data.compareData || undefined,
      });

      if (data.action === "refresh") {
        const refreshBtn = document.getElementById("refresh-btn");
        if (refreshBtn) refreshBtn.click();
      }
    } catch (err) {
      typingEl.remove();
      appendMessage("bot", `Connection issue: ${err.message}. Please try again.`);
    } finally {
      isSending = false;
    }
  }

  fab.addEventListener("click", () => {
    widget.classList.remove("hidden");
    fab.classList.add("hidden");
    if (!msgBox.children.length) showWelcome();
    input.focus();
  });

  closeBtn.addEventListener("click", () => {
    widget.classList.add("hidden");
    fab.classList.remove("hidden");
  });

  clearBtn.addEventListener("click", () => {
    chatHistory.length = 0;
    showWelcome();
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    sendChatMessage(input.value);
  });
})();

