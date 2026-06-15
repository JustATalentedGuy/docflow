import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  FileText,
  LogOut,
  MessageSquare,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  Upload,
} from "lucide-react";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE || "/api/v1";

function createClientId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `client-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

async function readResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  const text = await response.text();
  return { detail: text ? text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim() : "" };
}

function errorMessageFromResponse(response, payload, fallback) {
  const detail = typeof payload?.detail === "string" ? payload.detail : "";
  if (detail) return detail;
  if (response.status === 502) return "API gateway could not reach the backend. Restart the Docker stack and try again.";
  return fallback || `Request failed with ${response.status}`;
}

function useApi(token, onUnauthorized) {
  return useMemo(() => {
    async function request(path, options = {}) {
      const headers = new Headers(options.headers || {});
      if (token) headers.set("Authorization", `Bearer ${token}`);
      if (options.body && !(options.body instanceof FormData)) {
        headers.set("Content-Type", "application/json");
      }
      const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
      if (response.status === 401) onUnauthorized?.();
      if (!response.ok) {
        const payload = await readResponse(response).catch(() => ({}));
        throw new Error(errorMessageFromResponse(response, payload));
      }
      if (response.status === 204) return null;
      return readResponse(response);
    }
    return { request };
  }, [token, onUnauthorized]);
}

function AuthScreen({ onAuth }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      const payload = await readResponse(response);
      if (!response.ok) throw new Error(errorMessageFromResponse(response, payload, "Authentication failed"));
      onAuth(payload);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-panel">
        <div>
          <p className="eyebrow">Docflow</p>
          <h1>Document chats, scoped to each user.</h1>
        </div>
        <div className="segments">
          <button className={mode === "login" ? "active" : ""} onClick={() => setMode("login")}>
            Login
          </button>
          <button className={mode === "register" ? "active" : ""} onClick={() => setMode("register")}>
            Register
          </button>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label>
            Email
            <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" required />
          </label>
          <label>
            Password
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              minLength={8}
              required
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button className="primary" disabled={loading} type="submit">
            {loading ? "Working..." : mode === "login" ? "Login" : "Create account"}
          </button>
        </form>
      </section>
    </main>
  );
}

function FilesPanel({ api, selectedFileId, setSelectedFileId }) {
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef(null);

  async function loadFiles() {
    const data = await api.request("/files");
    setFiles(data);
  }

  useEffect(() => {
    loadFiles().catch(console.error);
  }, []);

  async function uploadFiles(event) {
    const selected = Array.from(event.target.files || []);
    if (!selected.length) return;
    setBusy(true);
    try {
      for (const file of selected) {
        const form = new FormData();
        form.append("file", file);
        await api.request("/upload", { method: "POST", body: form });
      }
      await loadFiles();
    } finally {
      setBusy(false);
      event.target.value = "";
    }
  }

  async function removeFile(fileId) {
    await api.request(`/files/${fileId}`, { method: "DELETE" });
    if (selectedFileId === fileId) setSelectedFileId("");
    await loadFiles();
  }

  return (
    <section className="pane files-pane">
      <header className="pane-head">
        <div>
          <h2>Files</h2>
          <p>{files.length} uploaded</p>
        </div>
        <div className="icon-row">
          <button title="Refresh files" className="icon-button" onClick={loadFiles}>
            <RefreshCw size={18} />
          </button>
          <button title="Upload files" className="icon-button" onClick={() => inputRef.current?.click()}>
            <Upload size={18} />
          </button>
        </div>
      </header>
      <input ref={inputRef} className="hidden" type="file" multiple onChange={uploadFiles} />
      <button className={`filter ${selectedFileId === "" ? "selected" : ""}`} onClick={() => setSelectedFileId("")}>
        All files
      </button>
      <div className="file-list">
        {files.map((file) => (
          <article className={`file-card ${selectedFileId === file.id ? "selected" : ""}`} key={file.id}>
            <button className="file-main" onClick={() => setSelectedFileId(file.id)}>
              <FileText size={18} />
              <span>
                <strong>{file.filename}</strong>
                <small>{file.status}</small>
              </span>
            </button>
            <button title="Delete file" className="icon-button danger" onClick={() => removeFile(file.id)}>
              <Trash2 size={16} />
            </button>
          </article>
        ))}
        {busy && <p className="muted">Uploading...</p>}
      </div>
    </section>
  );
}

function ChatsPanel({ api, activeChatId, setActiveChatId }) {
  const [chats, setChats] = useState([]);

  async function loadChats() {
    const data = await api.request("/chats");
    setChats(data);
    if (!activeChatId && data.length) setActiveChatId(data[0].id);
  }

  useEffect(() => {
    loadChats().catch(console.error);
  }, []);

  async function createChat() {
    const chat = await api.request("/chats", {
      method: "POST",
      body: JSON.stringify({ title: "New chat" }),
    });
    setChats((current) => [chat, ...current]);
    setActiveChatId(chat.id);
  }

  async function deleteChat(chatId) {
    await api.request(`/chats/${chatId}`, { method: "DELETE" });
    setChats((current) => current.filter((chat) => chat.id !== chatId));
    if (activeChatId === chatId) setActiveChatId("");
  }

  return (
    <section className="pane chats-pane">
      <header className="pane-head">
        <div>
          <h2>Chats</h2>
          <p>{chats.length} threads</p>
        </div>
        <button title="New chat" className="icon-button" onClick={createChat}>
          <Plus size={18} />
        </button>
      </header>
      <div className="chat-list">
        {chats.map((chat) => (
          <article className={`chat-card ${activeChatId === chat.id ? "selected" : ""}`} key={chat.id}>
            <button onClick={() => setActiveChatId(chat.id)}>
              <MessageSquare size={18} />
              <span>{chat.title}</span>
            </button>
            <button title="Delete chat" className="icon-button danger" onClick={() => deleteChat(chat.id)}>
              <Trash2 size={16} />
            </button>
          </article>
        ))}
      </div>
    </section>
  );
}

function ChatWorkspace({ api, activeChatId, selectedFileId }) {
  const [messages, setMessages] = useState([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const bottomRef = useRef(null);

  useEffect(() => {
    if (!activeChatId) {
      setMessages([]);
      return;
    }
    api
      .request(`/chats/${activeChatId}`)
      .then((chat) => setMessages(chat.messages))
      .catch(console.error);
  }, [activeChatId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!loading) {
      setElapsedSeconds(0);
      return undefined;
    }
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [loading]);

  async function sendMessage(event) {
    event.preventDefault();
    if (!query.trim() || !activeChatId) return;
    const userMessage = {
      id: createClientId(),
      role: "user",
      content: query.trim(),
      created_at: new Date().toISOString(),
    };
    setMessages((current) => [...current, userMessage]);
    setQuery("");
    setLoading(true);
    try {
      const response = await api.request(`/chats/${activeChatId}/messages`, {
        method: "POST",
        body: JSON.stringify({
          query: userMessage.content,
          top_k: 5,
          file_id: selectedFileId || null,
        }),
      });
      setMessages((current) => [
        ...current,
        {
          id: createClientId(),
          role: "assistant",
          content: response.answer,
          created_at: new Date().toISOString(),
          sources: response.sources,
        },
      ]);
    } catch (err) {
      setMessages((current) => [
        ...current,
        {
          id: createClientId(),
          role: "assistant",
          content: `Request failed: ${err.message}`,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="workspace">
      <header className="workspace-head">
        <div>
          <p className="eyebrow">Conversation</p>
          <h2>{activeChatId ? "Ask your documents" : "Select a chat"}</h2>
        </div>
        <span className="scope-pill">{selectedFileId ? "Single file scope" : "All files scope"}</span>
      </header>
      <div className="messages">
        {!activeChatId && <p className="empty">Create or select a chat to start asking questions.</p>}
        {messages.map((message) => (
          <article className={`message ${message.role}`} key={message.id}>
            <div>{message.content}</div>
            {message.sources?.length > 0 && (
              <details>
                <summary>{message.sources.length} sources</summary>
                {message.sources.map((source) => (
                  <p key={source.chunk_id}>
                    <strong>{source.filename}</strong>: {source.text}
                  </p>
                ))}
              </details>
            )}
          </article>
        ))}
        {loading && (
          <article className="message assistant pending">
            Searching your documents and preparing an answer...
            <small>{elapsedSeconds}s elapsed</small>
          </article>
        )}
        <div ref={bottomRef} />
      </div>
      <form className="composer" onSubmit={sendMessage}>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Ask about your uploaded documents"
          disabled={!activeChatId || loading}
        />
        <button title="Send message" className="primary icon-send" disabled={!activeChatId || loading}>
          <Send size={18} />
        </button>
      </form>
    </section>
  );
}

function App() {
  const [auth, setAuth] = useState(() => {
    const raw = localStorage.getItem("docflow-auth");
    return raw ? JSON.parse(raw) : null;
  });
  const [activeChatId, setActiveChatId] = useState("");
  const [selectedFileId, setSelectedFileId] = useState("");

  const logout = () => {
    localStorage.removeItem("docflow-auth");
    setAuth(null);
  };
  const api = useApi(auth?.token, logout);

  function handleAuth(payload) {
    localStorage.setItem("docflow-auth", JSON.stringify(payload));
    setAuth(payload);
  }

  if (!auth) return <AuthScreen onAuth={handleAuth} />;

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">D</div>
          <div>
            <p className="eyebrow">Docflow</p>
            <h1>{auth.user.email}</h1>
          </div>
          <button title="Logout" className="icon-button" onClick={logout}>
            <LogOut size={18} />
          </button>
        </div>
        <FilesPanel api={api} selectedFileId={selectedFileId} setSelectedFileId={setSelectedFileId} />
        <ChatsPanel api={api} activeChatId={activeChatId} setActiveChatId={setActiveChatId} />
      </aside>
      <ChatWorkspace api={api} activeChatId={activeChatId} selectedFileId={selectedFileId} />
    </main>
  );
}

createRoot(document.getElementById("root")).render(<App />);
