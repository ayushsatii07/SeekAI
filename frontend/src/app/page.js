"use client";

import { useEffect, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

const MODES = [
  { id: "web", label: "Web", icon: "🌐" },
  { id: "news", label: "News", icon: "⚡" },
  { id: "academic", label: "Academic", icon: "🎓" },
  { id: "hybrid", label: "Hybrid RRF", icon: "🔀" },
  { id: "local", label: "Local", icon: "📁" },
];

const SUGGESTIONS = [
  "Latest breakthroughs in AI agents",
  "Quantum computing commercial milestones",
  "Global semiconductor supply trends",
  "Recent discoveries in superconductivity",
];

export default function Home() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("web");
  const [freshness, setFreshness] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [pipelineStage, setPipelineStage] = useState("idle");
  const [stageMessage, setStageMessage] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState([]);
  const [metadata, setMetadata] = useState(null);
  const [highlightedSourceId, setHighlightedSourceId] = useState(null);
  const [copied, setCopied] = useState(false);

  // Auth & Settings state
  const [apiKey, setApiKey] = useState("");
  const [tenantInfo, setTenantInfo] = useState(null);
  const [showSettings, setShowSettings] = useState(false);

  // News Drawer state
  const [showNewsDrawer, setShowNewsDrawer] = useState(false);
  const [breakingNews, setBreakingNews] = useState([]);
  const [isCrawling, setIsCrawling] = useState(false);

  const textareaRef = useRef(null);

  useEffect(() => {
    const savedKey = localStorage.getItem("seekai_api_key") || "";
    setApiKey(savedKey);
    fetchTenantStatus(savedKey);
    fetchBreakingNews();
  }, []);

  const getAuthHeaders = (keyOverride) => {
    const key = keyOverride !== undefined ? keyOverride : apiKey;
    const headers = {
      "Content-Type": "application/json",
      "Accept": "text/event-stream, application/json",
    };
    if (key) {
      if (key.startsWith("sk_")) {
        headers["X-API-Key"] = key;
      } else {
        headers["Authorization"] = `Bearer ${key}`;
      }
    }
    return headers;
  };

  const fetchTenantStatus = async (keyToUse) => {
    try {
      const res = await fetch(`${API_BASE}/auth/me`, {
        headers: getAuthHeaders(keyToUse),
      });
      if (res.ok) {
        const data = await res.json();
        setTenantInfo(data);
      }
    } catch {
      // Quiet fail if backend not reachable
    }
  };

  const fetchBreakingNews = async () => {
    try {
      const res = await fetch(`${API_BASE}/crawler/articles?limit=20`, {
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        setBreakingNews(data);
      }
    } catch {
      // Quiet fail
    }
  };

  const triggerCrawlerRun = async () => {
    setIsCrawling(true);
    try {
      const res = await fetch(`${API_BASE}/crawler/run`, {
        method: "POST",
        headers: getAuthHeaders(),
      });
      if (res.ok) {
        await fetchBreakingNews();
      }
    } finally {
      setIsCrawling(false);
    }
  };

  const handleSaveApiKey = (newKey) => {
    setApiKey(newKey);
    localStorage.setItem("seekai_api_key", newKey);
    fetchTenantStatus(newKey);
    setShowSettings(false);
  };

  const handleSearch = async (queryText = query) => {
    const q = (queryText || "").trim();
    if (!q || isStreaming) return;

    setIsStreaming(true);
    setAnswer("");
    setSources([]);
    setMetadata(null);
    setPipelineStage("analyzing");
    setStageMessage("Analyzing question…");

    try {
      const response = await fetch(`${API_BASE}/ask`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({
          query: q,
          mode: mode,
          freshness: freshness || null,
          max_results: 10,
          stream: true,
        }),
      });

      if (!response.ok) {
        if (response.status === 429) {
          throw new Error("Rate limit quota exceeded. Please wait a moment.");
        }
        if (response.status === 401) {
          throw new Error("Unauthorized. Please check your API key in settings.");
        }
        throw new Error(`Search error (${response.status})`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n\n");
        buffer = lines.pop() || "";

        for (const block of lines) {
          if (!block.trim()) continue;
          let eventType = "message";
          let eventData = null;

          const blockLines = block.split("\n");
          for (const line of blockLines) {
            if (line.startsWith("event:")) {
              eventType = line.replace("event:", "").trim();
            } else if (line.startsWith("data:")) {
              try {
                eventData = JSON.parse(line.replace("data:", "").trim());
              } catch {
                eventData = line.replace("data:", "").trim();
              }
            }
          }

          if (eventType === "status" && eventData) {
            setPipelineStage(eventData.stage || "analyzing");
            setStageMessage(eventData.message || "");
          } else if (eventType === "sources" && Array.isArray(eventData)) {
            setSources(eventData);
          } else if (eventType === "token" && eventData?.delta) {
            setPipelineStage("synthesizing");
            setStageMessage("Synthesizing answer…");
            setAnswer((prev) => prev + eventData.delta);
          } else if (eventType === "done" && eventData) {
            setPipelineStage("done");
            setMetadata(eventData.metadata);
            if (eventData.sources?.length) {
              setSources(eventData.sources);
            }
            if (eventData.answer && !answer) {
              setAnswer(eventData.answer);
            }
            fetchTenantStatus();
          } else if (eventType === "error") {
            setStageMessage(`Error: ${eventData?.error || "Stream disconnected"}`);
          }
        }
      }
    } catch (err) {
      setAnswer(`⚠️ ${err.message || "Failed to reach search service."}`);
      setPipelineStage("idle");
    } finally {
      setIsStreaming(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSearch();
    }
  };

  const copyAnswer = () => {
    if (!answer) return;
    navigator.clipboard.writeText(answer);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const highlightSource = (num) => {
    setHighlightedSourceId(num);
    const el = document.getElementById(`source-card-${num}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  const renderAnswerWithCitations = (text) => {
    if (!text) return null;
    const parts = text.split(/(\[\d+\])/g);
    return parts.map((part, index) => {
      const match = part.match(/\[(\d+)\]/);
      if (match) {
        const citationNum = parseInt(match[1], 10);
        return (
          <span
            key={index}
            className="citation-chip"
            onClick={() => highlightSource(citationNum)}
            title={`View source [${citationNum}]`}
          >
            {citationNum}
          </span>
        );
      }
      return <span key={index}>{part}</span>;
    });
  };

  const hasResults = answer || isStreaming || sources.length > 0;

  return (
    <>
      {/* Top Navbar */}
      <nav className="navbar">
        <a className="brand-wrapper" href="/">
          <div className="brand-icon">S</div>
          <span className="brand-name">SeekAI</span>
        </a>

        <div className="nav-actions">
          {breakingNews.length > 0 && (
            <button
              className="btn-icon-pill"
              onClick={() => setShowNewsDrawer(true)}
              title="Live Breaking News Feeds"
            >
              <span className="pulse-dot" />
              <span>News ({breakingNews.length})</span>
            </button>
          )}

          {tenantInfo && (
            <button
              className="btn-icon-pill"
              onClick={() => setShowSettings(true)}
              title={`Tenant: ${tenantInfo.tenant.name}`}
            >
              <span style={{ color: "#34d399" }}>⚡</span>
              <span>{tenantInfo.rate_limit_remaining} left</span>
            </button>
          )}

          <button
            className="btn-icon-pill"
            onClick={() => setShowSettings(true)}
            title="API Key Settings"
          >
            ⚙️
          </button>
        </div>
      </nav>

      {/* Main Content */}
      <main className="container">
        {/* Minimal Hero Header */}
        <section className={`hero-wrapper ${hasResults ? "compact" : ""}`}>
          <h1 className="hero-title-clean">
            {hasResults ? "Ask another question" : "Where knowledge begins."}
          </h1>
        </section>

        {/* Unified Search Omnibar (Perplexity Style) */}
        <div className="omnibar-card">
          <textarea
            ref={textareaRef}
            rows={1}
            className="omnibar-textarea"
            placeholder="Ask anything or research a topic..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isStreaming}
          />

          <div className="omnibar-bottom-row">
            <div className="omnibar-controls-left">
              {/* Mode Select */}
              <select
                className="control-pill-select active"
                value={mode}
                onChange={(e) => setMode(e.target.value)}
              >
                {MODES.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.icon} {m.label}
                  </option>
                ))}
              </select>

              {/* Freshness Select */}
              <select
                className="control-pill-select"
                value={freshness}
                onChange={(e) => setFreshness(e.target.value)}
              >
                <option value="">🕒 Any time</option>
                <option value="day">🕒 Past 24h</option>
                <option value="week">🕒 Past week</option>
                <option value="month">🕒 Past month</option>
                <option value="year">🕒 Past year</option>
              </select>
            </div>

            <button
              type="button"
              className="submit-arrow-btn"
              onClick={() => handleSearch()}
              disabled={!query.trim() || isStreaming}
              title="Search (Enter)"
            >
              {isStreaming ? "…" : "↑"}
            </button>
          </div>
        </div>

        {/* Clean Suggestions (Only before first search) */}
        {!hasResults && (
          <div className="suggestions-row">
            {SUGGESTIONS.map((item, idx) => (
              <button
                key={idx}
                className="suggestion-chip"
                onClick={() => {
                  setQuery(item);
                  handleSearch(item);
                }}
              >
                {item}
              </button>
            ))}
          </div>
        )}

        {/* Minimal Live Pipeline Indicator */}
        {isStreaming && (
          <div className="pipeline-bar">
            <div className="spinner" />
            <span>{stageMessage || "Searching…"}</span>
          </div>
        )}

        {/* Sources Top Carousel */}
        {sources.length > 0 && (
          <div className="sources-container">
            <div className="sources-heading">
              <span>📚 Sources ({sources.length})</span>
            </div>
            <div className="sources-compact-grid">
              {sources.map((s, idx) => {
                const sourceNum = s.citation_number || s.id || idx + 1;
                const isTarget = highlightedSourceId === sourceNum;
                return (
                  <a
                    key={s.id || idx}
                    id={`source-card-${sourceNum}`}
                    href={s.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={`source-compact-card ${isTarget ? "active-target" : ""}`}
                    onMouseEnter={() => setHighlightedSourceId(sourceNum)}
                    onMouseLeave={() => setHighlightedSourceId(null)}
                  >
                    <div>
                      <div className="source-domain-clean">
                        <span>{s.domain || "Web"}</span>
                        <span>[{sourceNum}]</span>
                      </div>
                      <div className="source-title-clean">{s.title || "Source"}</div>
                    </div>
                  </a>
                );
              })}
            </div>
          </div>
        )}

        {/* Answer Box */}
        {(answer || isStreaming) && (
          <div className="answer-box">
            <div className="answer-topbar">
              <span style={{ color: "#38bdf8", fontWeight: "600" }}>Answer</span>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                {metadata && <span>⏱️ {metadata.response_time}s</span>}
                {answer && (
                  <button
                    className="btn-icon-pill"
                    onClick={copyAnswer}
                    style={{ padding: "3px 8px", fontSize: "0.75rem" }}
                  >
                    {copied ? "✓ Copied" : "Copy"}
                  </button>
                )}
              </div>
            </div>

            <div className="answer-text">
              {renderAnswerWithCitations(answer)}
              {isStreaming && <span className="cursor-blink" />}
            </div>
          </div>
        )}
      </main>

      {/* Breaking News Sidebar Drawer */}
      {showNewsDrawer && (
        <>
          <div className="drawer-backdrop" onClick={() => setShowNewsDrawer(false)} />
          <div className="drawer">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <h3 style={{ fontSize: "1.1rem", fontWeight: "700" }}>⚡ Breaking News</h3>
              <button
                className="btn-icon-pill"
                onClick={() => setShowNewsDrawer(false)}
              >
                ✕
              </button>
            </div>

            <button
              className="btn-icon-pill"
              style={{ width: "100%", justifyContent: "center", marginBottom: "14px", padding: "8px" }}
              onClick={triggerCrawlerRun}
              disabled={isCrawling}
            >
              {isCrawling ? "Crawling Feeds…" : "🔄 Fetch Latest Feeds"}
            </button>

            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {breakingNews.map((art, idx) => (
                <div
                  key={idx}
                  style={{
                    padding: "10px 12px",
                    background: "rgba(255,255,255,0.03)",
                    border: "1px solid rgba(255,255,255,0.06)",
                    borderRadius: "10px",
                    cursor: "pointer",
                  }}
                  onClick={() => {
                    setQuery(art.title);
                    setMode("news");
                    setShowNewsDrawer(false);
                    handleSearch(art.title);
                  }}
                >
                  <div style={{ fontSize: "0.72rem", color: "#64748b", marginBottom: "2px" }}>
                    {art.domain}
                  </div>
                  <div style={{ fontSize: "0.85rem", fontWeight: "600", color: "#f8fafc" }}>
                    {art.title}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* Settings / Auth Modal */}
      {showSettings && (
        <div className="modal-overlay" onClick={() => setShowSettings(false)}>
          <div className="modal-card" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
              <h3 style={{ fontSize: "1.1rem", fontWeight: "700" }}>Settings & Auth</h3>
              <button className="btn-icon-pill" onClick={() => setShowSettings(false)}>✕</button>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ fontSize: "0.8rem", color: "#94a3b8" }}>API Key or JWT Token</label>
              <input
                type="password"
                className="form-input"
                placeholder="sk_live_... or JWT token"
                defaultValue={apiKey}
                id="api-key-input"
              />
            </div>

            {tenantInfo && (
              <div style={{ padding: "12px", background: "rgba(255,255,255,0.03)", borderRadius: "8px", fontSize: "0.8rem", color: "#94a3b8", marginBottom: "16px" }}>
                <div>Tenant: <strong style={{ color: "#fff" }}>{tenantInfo.tenant.name}</strong> ({tenantInfo.tenant.tier})</div>
                <div>Quota: <strong style={{ color: "#38bdf8" }}>{tenantInfo.rate_limit_remaining} / {tenantInfo.rate_limit_limit} req/min</strong></div>
              </div>
            )}

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
              <button className="btn-icon-pill" onClick={() => setShowSettings(false)}>Cancel</button>
              <button
                className="btn-icon-pill"
                style={{ background: "#38bdf8", color: "#000", fontWeight: "600", borderColor: "#38bdf8" }}
                onClick={() => {
                  const val = document.getElementById("api-key-input").value;
                  handleSaveApiKey(val);
                }}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
