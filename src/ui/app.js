const history = document.querySelector("#history");
const envelope = document.querySelector("#envelope");
const form = document.querySelector("#composer");
const prompt = document.querySelector("#prompt");
const pack = document.querySelector("#pack");

function addMessage(role, text) {
  const node = document.createElement("div");
  node.className = `message ${role}`;
  node.textContent = text;
  history.append(node);
  history.scrollTop = history.scrollHeight;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const userText = prompt.value.trim();
  if (!userText) {
    return;
  }
  addMessage("user", userText);
  prompt.value = "";
  let response;
  let raw;
  try {
    response = await fetch("/api/v1/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_text: userText, pack_id: pack.value }),
    });
    raw = await response.text();
  } catch (error) {
    addMessage("agent", "The request failed.");
    envelope.textContent = String(error);
    return;
  }
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    addMessage("agent", `HTTP ${response.status}`);
    envelope.textContent = raw;
    return;
  }
  envelope.textContent = JSON.stringify(body, null, 2);
  const chatText = body?.content?.chat_text;
  addMessage("agent", typeof chatText === "string" ? chatText : `HTTP ${response.status}`);
});
