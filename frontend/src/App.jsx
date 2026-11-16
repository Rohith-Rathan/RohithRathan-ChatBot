import { useState, useRef, useEffect } from "react";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/+$/, "");

const SUGGESTIONS = [
  "How many times did Rohith submit the assignment?",
  "Who are the top submitters?",
  "How many submissions were made each day?",
  "When did Shalini last submit?",
];

function toText(v) {
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function ask(text) {
    text = text.trim();
    if (!text || loading) return;
    const next = [...messages, { role: "user", content: text }];
    setMessages(next);
    setInput("");
    setLoading(true);
    let reply, isError = false;
    try {
      // Only send plain role/content pairs, never error bubbles
      const history = next.filter((m) => !m.error).map(({ role, content }) => ({ role, content }));
      const requestStart = performance.now();
      console.log("[Chat] Sending request to backend...");
      const res = await fetch(`${API_BASE_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history }),
      });
      const requestEnd = performance.now();
      console.log(
        `[Chat] Backend total response time: ${((requestEnd - requestStart) / 1000).toFixed(2)} seconds`
      );
      const raw = await res.text();
      let data = null;
      try {
        data = JSON.parse(raw);
      } catch {}
      if (!res.ok || !data) {
        isError = true;
        reply = `Backend returned HTTP ${res.status}. Check that VITE_API_BASE_URL points to the deployed chat API.`;
      } else {
        reply = toText(data.reply ?? "No reply received.");
        isError = !!data.error;
      }
    } catch {
      isError = true;
      reply = "Could not reach the chat API. Check the backend URL and that the API service is running.";
    }
    setMessages([...next, { role: "assistant", content: reply, error: isError }]);
    setLoading(false);
  }

  return (
    <div className="app">
      <header>
        <div className="logo">TC</div>
        <div>
          <h1>Assignment Tracker</h1>
          <p>Ask anything about learner submissions</p>
        </div>
        {messages.length > 0 && (
          <button className="clear" onClick={() => setMessages([])}>
            New chat
          </button>
        )}
      </header>

      <main>
        {messages.length === 0 ? (
          <div className="welcome">
            <div className="bot big">AI</div>
            <h2>How can I help?</h2>
            <p>I look up live data from the Trainer Central dashboard.</p>
            <div className="chips">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => ask(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="log">
            {messages.map((m, i) => (
              <div key={i} className={"row " + m.role}>
                {m.role === "assistant" && <div className="bot">AI</div>}
                <div className={"bubble" + (m.error ? " error" : "")}>{m.content}</div>
              </div>
            ))}
            {loading && (
              <div className="row assistant">
                <div className="bot">AI</div>
                <div className="bubble typing">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            )}
            <div ref={endRef} />
          </div>
        )}
      </main>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          ask(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about submissions..."
          autoFocus
        />
        <button type="submit" disabled={loading || !input.trim()} aria-label="Send">
          ➤
        </button>
      </form>
    </div>
  );
}
