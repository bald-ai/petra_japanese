(function () {
  const body = document.body;
  if (!body || !body.dataset.lessonKey) {
    return;
  }

  const lessonName = body.dataset.lessonName || "lesson";
  const history = [
    {
      role: "system",
      text:
        "You are a concise, friendly assistant who helps students understand the lesson content. Keep answers focused on the provided materials and give short explanations.",
    },
  ];

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "lesson-chat-toggle";
  toggle.setAttribute("aria-label", "Toggle lesson chat assistant");
  toggle.textContent = "💬";

  const container = document.createElement("section");
  container.className = "lesson-chat-window";
  container.setAttribute("role", "dialog");
  container.setAttribute("aria-label", `${lessonName} AI helper`);

  const log = document.createElement("div");
  log.className = "lesson-chat-log";

  const form = document.createElement("form");
  form.className = "lesson-chat-form";
  form.noValidate = true;

  const input = document.createElement("input");
  input.className = "lesson-chat-input";
  input.type = "text";
  input.placeholder = "Ask about this lesson...";
  input.autocomplete = "off";

  const send = document.createElement("button");
  send.className = "lesson-chat-send";
  send.type = "submit";
  send.textContent = "Send";

  form.append(input, send);
  container.append(log, form);
  body.append(toggle, container);

  function appendRow(role, text) {
    const row = document.createElement("div");
    row.className = `lesson-chat-row ${role}`;
    row.textContent = text;
    log.appendChild(row);
    log.scrollTop = log.scrollHeight;
    return row;
  }

  function setPending(isPending) {
    input.disabled = isPending;
    send.disabled = isPending;
    if (!isPending) {
      input.focus();
    }
  }

  async function requestReply(message) {
    history.push({ role: "user", text: message });
    appendRow("me", message);
    const assistantNode = appendRow("bot", "...");

    setPending(true);

    let response;
    try {
      response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ history, model: "gpt-5-2025-08-07" }),
      });
    } catch (error) {
      assistantNode.classList.add("meta");
      assistantNode.textContent = "Network error. Please try again.";
      setPending(false);
      return;
    }

    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      assistantNode.classList.add("meta");
      assistantNode.textContent = "Invalid response from server.";
      setPending(false);
      return;
    }

    setPending(false);

    if (!response.ok || !payload || typeof payload.text !== "string") {
      assistantNode.classList.add("meta");
      let detail = "";
      if (payload) {
        const message = payload.message;
        if (typeof message === "string" && message.trim()) {
          detail = ` (${message.trim()})`;
        } else if (payload.error) {
          if (typeof payload.error === "string") {
            detail = ` (${payload.error})`;
          }
        } else if (payload.details && payload.details.error) {
          const err = payload.details.error;
          if (typeof err === "string") {
            detail = ` (${err})`;
          } else if (err && typeof err.message === "string") {
            detail = ` (${err.message})`;
          }
        }
      }
      assistantNode.textContent = `Could not get a reply${detail}.`;
      return;
    }

    assistantNode.textContent = payload.text.trim() || "I do not have an answer right now.";
    history.push({ role: "assistant", text: assistantNode.textContent });
  }

  toggle.addEventListener("click", () => {
    container.classList.toggle("open");
    if (container.classList.contains("open")) {
      input.focus();
    }
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || send.disabled) {
      return;
    }
    input.value = "";
    requestReply(message);
  });
})();
