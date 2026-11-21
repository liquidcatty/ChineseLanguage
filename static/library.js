(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const el = {
    list: $("#story-list"),
    count: $("#library-count"),
    level: $("#library-level"),
    search: $("#library-search"),
    status: $("#library-status"),
    modeReading: $("#mode-reading"),
    modeListening: $("#mode-listening"),
  };

  const state = {
    stories: [],
    level: "",
    query: "",
    mode: "read",
  };

  function setStatus(message, isError = false) {
    el.status.textContent = message || "";
    el.status.classList.toggle("error", Boolean(isError));
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) {
      let detail = response.statusText || "request failed";
      try {
        const body = await response.json();
        if (body && body.detail) {
          detail =
            typeof body.detail === "string"
              ? body.detail
              : JSON.stringify(body.detail);
        }
      } catch (error) {
        /* response was not JSON */
      }
      throw new Error(detail);
    }
    return response.json();
  }

  async function setFinished(storyId, finished) {
    return fetchJson(
      "/api/stories/" + encodeURIComponent(storyId) + "/finished",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ finished }),
      }
    );
  }

  function levelValue(level) {
    const text = String(level ?? "");
    return text === "7-9" ? 7 : Number(text) || 99;
  }

  function storyTile(item) {
    const tile = document.createElement("div");
    tile.className = "story-tile";
    tile.dataset.id = item.id;

    const link = document.createElement("a");
    link.className = "story-link";
    link.href =
      (state.mode === "listen" ? "/listen/" : "/read/") +
      encodeURIComponent(item.id);

    const title = document.createElement("span");
    title.className = "story-title";
    title.textContent = item.title || item.id;

    const meta = document.createElement("span");
    meta.className = "story-meta";
    const parts = [];
    if (item.seed_noun) parts.push(item.seed_noun);
    if (item.n_han) parts.push(item.n_han + " 字");
    meta.textContent = parts.join(" · ");
    if (item.n_questions) {
      const badge = document.createElement("span");
      badge.className = "story-badge";
      badge.textContent = "quiz";
      if (meta.textContent) meta.append(" · ");
      meta.appendChild(badge);
    }
    link.append(title, meta);
    tile.appendChild(link);

    const finishedLabel = document.createElement("label");
    finishedLabel.className = "finished-label";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.className = "finished-check";
    checkbox.checked = Boolean(item.finished);
    checkbox.addEventListener("change", async (event) => {
      event.stopPropagation();
      const value = checkbox.checked;
      try {
        await setFinished(item.id, value);
        item.finished = value;
      } catch (error) {
        checkbox.checked = !value;
        setStatus("Could not save finished state: " + error.message, true);
      }
    });
    const text = document.createElement("span");
    text.textContent = "Finished";
    finishedLabel.append(checkbox, text);
    tile.appendChild(finishedLabel);

    return tile;
  }

  function render() {
    const query = state.query.trim().toLowerCase();
    const items = state.stories.filter((item) => {
      if (state.level && String(item.level) !== state.level) return false;
      if (!query) return true;
      return (
        String(item.title || "").toLowerCase().includes(query) ||
        String(item.seed_noun || "").toLowerCase().includes(query)
      );
    });
    items.sort((a, b) => {
      const byLevel = levelValue(a.level) - levelValue(b.level);
      if (byLevel) return byLevel;
      return String(a.seed_noun || a.title || "").localeCompare(
        String(b.seed_noun || b.title || ""),
        "zh-Hans-CN"
      );
    });

    const total = state.stories.length;
    el.count.textContent =
      items.length === total
        ? total + (total === 1 ? " story" : " stories")
        : items.length + " of " + total + " stories";

    el.list.innerHTML = "";
    if (!items.length) {
      setStatus("No stories match.");
      return;
    }
    setStatus("");

    const groups = new Map();
    items.forEach((item) => {
      const key = item.level === null || item.level === undefined ? "?" : String(item.level);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    });

    [...groups.keys()]
      .sort((a, b) => levelValue(a) - levelValue(b))
      .forEach((level) => {
        const stories = groups.get(level);
        const section = document.createElement("section");
        section.className = "story-group";
        const heading = document.createElement("h2");
        heading.className = "story-group-head";
        heading.textContent =
          "HSK " + level + " · " + stories.length + (stories.length === 1 ? " story" : " stories");
        section.appendChild(heading);
        const grid = document.createElement("div");
        grid.className = "story-grid";
        stories.forEach((item) => grid.appendChild(storyTile(item)));
        section.appendChild(grid);
        el.list.appendChild(section);
      });
  }

  function setMode(mode) {
    state.mode = mode;
    const reading = mode === "read";
    el.modeReading.classList.toggle("active", reading);
    el.modeListening.classList.toggle("active", !reading);
    el.modeReading.setAttribute("aria-selected", String(reading));
    el.modeListening.setAttribute("aria-selected", String(!reading));
    render();
  }

  el.modeReading.addEventListener("click", () => setMode("read"));
  el.modeListening.addEventListener("click", () => setMode("listen"));
  el.level.addEventListener("change", () => {
    state.level = el.level.value;
    render();
  });
  el.search.addEventListener("input", () => {
    state.query = el.search.value;
    render();
  });

  setStatus("Loading stories…");
  fetchJson("/api/stories")
    .then((items) => {
      state.stories = items;
      render();
    })
    .catch((error) => setStatus("Could not load the library: " + error.message, true));
})();
