(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const PINYIN_MODES = ["off", "new", "all"];
  const PINYIN_LABELS = {
    off: "Pinyin: off",
    new: "Pinyin: new words",
    all: "Pinyin: all",
  };

  const el = {
    pinyin: $("#pinyin-toggle"),
    status: $("#status"),
    reader: $("#reader"),
    title: $("#title"),
    meta: $("#story-meta"),
    finished: $("#finished-check"),
    empty: $("#empty"),
    story: $("#story"),
    quiz: $("#quiz"),
    questions: $("#questions"),
    check: $("#check"),
    score: $("#score"),
    popover: $("#popover"),
  };

  const state = {
    story: null,
    pinyin: readMode(),
    answers: [],
    result: null,
    tokenSeq: 0,
  };

  function readMode() {
    try {
      const value = localStorage.getItem("hsk.pinyin");
      return PINYIN_MODES.includes(value) ? value : "off";
    } catch (error) {
      return "off";
    }
  }

  function savePreference(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (error) {
      /* private mode or storage disabled */
    }
  }

  function setStatus(message, isError = false) {
    el.status.textContent = message || "";
    el.status.classList.toggle("error", Boolean(isError));
  }

  const OPEN_PUNCT = new Set(["“", "‘", "（", "《", "〈", "【", "(", "[", "「", "『"]);

  function groupTokens(tokens) {
    const groups = [];
    let prefix = "";
    let current = null;
    tokens.forEach((token) => {
      if (token.status === "punct") {
        if (OPEN_PUNCT.has(token.w)) {
          if (current) {
            groups.push(current);
            current = null;
          }
          prefix += token.w;
        } else if (current) {
          current.suffix += token.w;
        } else {
          prefix += token.w;
        }
        return;
      }
      if (current) groups.push(current);
      current = { token, prefix, suffix: "" };
      prefix = "";
    });
    if (current) groups.push(current);
    else if (prefix) groups.push({ token: null, prefix, suffix: "" });
    return groups;
  }

  function makeGroupSpan(group, allowRuby) {
    const token = group.token;
    const span = document.createElement("span");
    span.className = "w";
    span.dataset.i = String(state.tokenSeq++);
    if (!token) {
      span.textContent = group.prefix + group.suffix;
      return span;
    }
    if (token.status === "oov") span.classList.add("oov");
    span._token = token;
    span.dataset.w = token.w;

    if (group.prefix) {
      span.appendChild(document.createTextNode(group.prefix + "\u2060"));
    }

    const chars = Array.from(token.w || "");
    const syllables = Array.isArray(token.pinyin) ? token.pinyin : [];
    const eligible =
      token.status !== "punct" &&
      token.status !== "digit" &&
      token.status !== "latin" &&
      syllables.length === chars.length;
    const withRuby =
      allowRuby &&
      eligible &&
      (state.pinyin === "all" ||
        (state.pinyin === "new" && token.status === "oov"));

    if (withRuby) {
      chars.forEach((character, index) => {
        const ruby = document.createElement("ruby");
        ruby.appendChild(document.createTextNode(character));
        const rt = document.createElement("rt");
        rt.textContent = syllables[index] || "";
        ruby.appendChild(rt);
        span.appendChild(ruby);
      });
    } else {
      span.appendChild(document.createTextNode(token.w));
    }

    if (group.suffix) {
      span.appendChild(document.createTextNode("\u2060" + group.suffix));
    }

    span.addEventListener("click", (event) => {
      event.stopPropagation();
      if (!syllables.length) return;
      openPopover(span, token);
    });
    return span;
  }

  function openPopover(word, token) {
    const pop = el.popover;
    pop.querySelector(".pop-char").textContent = token.w || "";
    pop.querySelector(".pop-pinyin").textContent = (token.pinyin || []).join(" ");
    pop.querySelector(".pop-gloss").textContent = token.gloss_pl || "—";
    const english = pop.querySelector(".pop-en");
    english.textContent = token.gloss_en || "";
    english.hidden = !token.gloss_en;
    pop.querySelector(".pop-level").textContent =
      token.level === null || token.level === undefined
        ? "—"
        : "HSK " + token.level;

    pop.hidden = false;
    const anchor = word.getBoundingClientRect();
    const box = pop.getBoundingClientRect();
    const margin = 8;
    let left = anchor.left + anchor.width / 2 - box.width / 2;
    let top = anchor.bottom + margin;
    if (top + box.height > window.innerHeight - margin) {
      top = anchor.top - box.height - margin;
    }
    left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));
    top = Math.max(margin, Math.min(top, window.innerHeight - box.height - margin));
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  function closePopover() {
    el.popover.hidden = true;
  }

  document.addEventListener("click", (event) => {
    if (!el.popover.hidden && !el.popover.contains(event.target)) closePopover();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePopover();
  });
  window.addEventListener("scroll", closePopover, { passive: true, capture: true });
  window.addEventListener("resize", closePopover);

  function appendTokens(parent, tokens, fallback, allowRuby = true) {
    if (Array.isArray(tokens) && tokens.length) {
      groupTokens(tokens).forEach((group) =>
        parent.appendChild(makeGroupSpan(group, allowRuby))
      );
    } else {
      parent.appendChild(document.createTextNode(fallback || ""));
    }
  }

  function renderStory() {
    el.story.innerHTML = "";
    state.tokenSeq = 0;
    const data = state.story;
    if (!data) return;

    el.title.textContent = data.title || "";
    const parts = [];
    if (data.level !== null && data.level !== undefined) parts.push("HSK " + data.level);
    if (data.metrics && data.metrics.n_han) parts.push(data.metrics.n_han + " 字");
    if (data.seed_noun) parts.push(data.seed_noun);
    el.meta.textContent = parts.join(" · ");

    el.empty.hidden = true;
    el.story.hidden = false;
    const sentences = Array.isArray(data.sentences) ? data.sentences : [];
    const allTokens = Array.isArray(data.tokens) ? data.tokens : [];
    sentences.forEach((sentence, sentenceIndex) => {
      const paragraph = document.createElement("p");
      paragraph.className = "sentence";
      paragraph.dataset.s = String(sentenceIndex);
      const tokens = allTokens[sentenceIndex];
      if (Array.isArray(tokens) && tokens.length) {
        appendTokens(paragraph, tokens, sentence, true);
      } else {
        paragraph.textContent = sentence;
      }
      el.story.appendChild(paragraph);
    });
  }

  function renderQuiz() {
    el.questions.innerHTML = "";
    const questions =
      state.story && Array.isArray(state.story.questions) ? state.story.questions : [];
    if (!questions.length) {
      el.quiz.hidden = true;
      el.score.textContent = "";
      return;
    }
    el.quiz.hidden = false;
    if (state.answers.length !== questions.length) {
      state.answers = questions.map((_, index) => state.answers[index] || "");
    }

    questions.forEach((question, questionIndex) => {
      const box = document.createElement("div");
      box.className = "q";
      box.dataset.q = String(questionIndex);

      const text = document.createElement("p");
      text.className = "q-text";
      const number = document.createElement("span");
      number.className = "q-num";
      number.textContent = questionIndex + 1 + ".";
      text.appendChild(number);
      appendTokens(text, question.question_tokens, question.question || "", false);
      box.appendChild(text);

      const options = document.createElement("div");
      options.className = "options";
      const labels =
        question.type === "tf"
          ? ["对", "错"]
          : ["A", "B", "C", "D"].slice(0, (question.options || []).length);

      labels.forEach((label, optionIndex) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "opt";
        button.dataset.opt = label;

        const tag = document.createElement("span");
        tag.className = "opt-label";
        tag.textContent = question.type === "tf" ? "" : label + ".";
        if (tag.textContent) button.appendChild(tag);

        const content = document.createElement("span");
        if (question.type === "tf") {
          content.textContent = label;
        } else {
          const tokens =
            question.option_tokens && question.option_tokens[optionIndex];
          appendTokens(content, tokens, (question.options || [])[optionIndex] || "", false);
        }
        button.appendChild(content);
        button.addEventListener("click", () => selectAnswer(questionIndex, label));
        options.appendChild(button);
      });

      box.appendChild(options);
      el.questions.appendChild(box);
    });

    applySelection();
    if (state.result) {
      applyResult(state.result, false);
      el.score.textContent =
        "Score: " + state.result.score + "/" + state.result.total;
    } else {
      el.score.textContent = "";
    }
  }

  function selectAnswer(questionIndex, label) {
    state.answers[questionIndex] = label;
    state.result = null;
    el.score.textContent = "";
    el.questions
      .querySelectorAll(".opt.correct, .opt.wrong")
      .forEach((node) => node.classList.remove("correct", "wrong"));
    el.story
      .querySelectorAll(".sentence.evidence")
      .forEach((node) => node.classList.remove("evidence"));
    applySelection();
  }

  function applySelection() {
    el.questions.querySelectorAll(".q").forEach((box) => {
      const questionIndex = Number(box.dataset.q);
      box.querySelectorAll(".opt").forEach((button) => {
        button.classList.toggle(
          "selected",
          button.dataset.opt === state.answers[questionIndex]
        );
      });
    });
  }

  function applyResult(result, shouldScroll = true) {
    const questions =
      state.story && Array.isArray(state.story.questions) ? state.story.questions : [];
    el.story
      .querySelectorAll(".sentence.evidence")
      .forEach((node) => node.classList.remove("evidence"));

    questions.forEach((question, questionIndex) => {
      const response = result.results && result.results[questionIndex];
      const box = el.questions.querySelector('.q[data-q="' + questionIndex + '"]');
      if (!response || !box) return;
      box.querySelectorAll(".opt").forEach((button) => {
        button.classList.remove("correct", "wrong");
        const value = button.dataset.opt;
        if (value === response.correct_answer) button.classList.add("correct");
        if (!response.is_correct && value === response.user_answer) {
          button.classList.add("wrong");
        }
      });
    });

    const indices = new Set();
    (result.results || []).forEach((response, questionIndex) => {
      if (response.is_correct) return;
      const evidence = (questions[questionIndex] && questions[questionIndex].evidence) || [];
      evidence.forEach((index) => indices.add(index));
    });

    let first = null;
    indices.forEach((index) => {
      const sentence = el.story.querySelector('.sentence[data-s="' + index + '"]');
      if (!sentence) return;
      sentence.classList.add("evidence");
      if (first === null) first = sentence;
    });
    if (first && shouldScroll) {
      first.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function setPinyin(mode) {
    state.pinyin = mode;
    savePreference("hsk.pinyin", mode);
    el.pinyin.textContent = PINYIN_LABELS[mode];
    closePopover();
    renderStory();
    renderQuiz();
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

  async function checkAnswers() {
    if (!state.story || !state.story.id) return;
    el.check.disabled = true;
    el.score.textContent = "Checking…";
    try {
      const result = await fetchJson("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          story_id: state.story.id,
          answers: state.answers,
        }),
      });
      state.result = result;
      applyResult(result);
      el.score.textContent = "Score: " + result.score + "/" + result.total;
    } catch (error) {
      el.score.textContent = "";
      setStatus("Could not check answers: " + error.message, true);
    } finally {
      el.check.disabled = false;
    }
  }

  function fail(message) {
    setStatus(message, true);
    el.title.textContent = "";
    el.meta.textContent = "";
    el.finished.checked = false;
    el.finished.disabled = true;
    el.empty.hidden = false;
    el.story.hidden = true;
    el.quiz.hidden = true;
  }

  async function saveFinished(finished) {
    if (!state.story || !state.story.id) return;
    try {
      await fetchJson(
        "/api/stories/" + encodeURIComponent(state.story.id) + "/finished",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ finished }),
        }
      );
      state.story.finished = finished;
    } catch (error) {
      el.finished.checked = !finished;
      setStatus("Could not save finished state: " + error.message, true);
    }
  }

  async function loadStory() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    const storyId = decodeURIComponent(parts[parts.length - 1] || "");
    if (!storyId || storyId === "read") {
      fail("Story not found.");
      return;
    }
    setStatus("Loading story…");
    try {
      const data = await fetchJson("/api/stories/" + encodeURIComponent(storyId));
      state.story = data;
      state.answers = [];
      state.result = null;
      el.finished.checked = Boolean(data.finished);
      el.finished.disabled = false;
      renderStory();
      renderQuiz();
      document.title =
        (data.title || "Story") +
        (data.level !== null && data.level !== undefined ? " · HSK " + data.level : "");
      setStatus("");
      window.scrollTo({ top: 0, behavior: "auto" });
    } catch (error) {
      fail("Could not load the story: " + error.message);
    }
  }

  el.pinyin.textContent = PINYIN_LABELS[state.pinyin];
  el.pinyin.addEventListener("click", () => {
    const index = PINYIN_MODES.indexOf(state.pinyin);
    setPinyin(PINYIN_MODES[(index + 1) % PINYIN_MODES.length]);
  });
  el.finished.addEventListener("change", () => saveFinished(el.finished.checked));
  el.check.addEventListener("click", checkAnswers);
  loadStory();
})();
