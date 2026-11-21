(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const el = {
    title: $("#title"),
    meta: $("#story-meta"),
    finished: $("#finished-check"),
    status: $("#status"),
    empty: $("#empty"),
    controls: $(".listen-controls"),
    play: $("#play"),
    transcriptToggle: $("#transcript-toggle"),
    transcript: $("#transcript"),
    listenStatus: $("#listen-status"),
    quiz: $("#quiz"),
    questions: $("#questions"),
    check: $("#check"),
    score: $("#score"),
  };

  const state = { story: null, answers: [], result: null };

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

  function setPlaying(playing) {
    el.play.textContent = playing ? "Stop" : "Play";
    el.play.classList.toggle("playing", playing);
  }

  function playStory() {
    if (!state.story) return;
    if (!("speechSynthesis" in window)) {
      el.listenStatus.textContent = "Speech synthesis is not available in this browser.";
      el.play.disabled = true;
      return;
    }
    if (window.speechSynthesis.speaking) {
      window.speechSynthesis.cancel();
      setPlaying(false);
      el.listenStatus.textContent = "Stopped.";
      return;
    }

    const text = (state.story.sentences || []).join("");
    if (!text) {
      el.listenStatus.textContent = "No text to play.";
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = 0.9;
    utterance.onend = () => {
      setPlaying(false);
      el.listenStatus.textContent = "Finished.";
    };
    utterance.onerror = (event) => {
      setPlaying(false);
      el.listenStatus.textContent = "Audio error: " + (event.error || "unknown");
    };
    window.speechSynthesis.speak(utterance);
    setPlaying(true);
    el.listenStatus.textContent = "Playing…";
  }

  function renderTranscript() {
    el.transcript.innerHTML = "";
    (state.story.sentences || []).forEach((sentence) => {
      const paragraph = document.createElement("p");
      paragraph.textContent = sentence;
      el.transcript.appendChild(paragraph);
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
      text.textContent =
        questionIndex + 1 + ". " + (question.question || question.statement || "");
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
        content.textContent =
          question.type === "tf"
            ? label
            : (question.options || [])[optionIndex] || "";
        button.appendChild(content);
        button.addEventListener("click", () => selectAnswer(questionIndex, label));
        options.appendChild(button);
      });

      box.appendChild(options);
      el.questions.appendChild(box);
    });

    applySelection();
    if (state.result) {
      applyResult(state.result);
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
    el.questions
      .querySelectorAll(".quiz-evidence")
      .forEach((node) => node.remove());
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

  function applyResult(result) {
    const questions =
      state.story && Array.isArray(state.story.questions) ? state.story.questions : [];
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
      box.querySelectorAll(".quiz-evidence").forEach((node) => node.remove());
      if (!response.is_correct && (response.evidence_sentences || []).length) {
        const evidence = document.createElement("p");
        evidence.className = "quiz-evidence";
        evidence.textContent = "依据：" + response.evidence_sentences.join(" ");
        box.appendChild(evidence);
      }
    });
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
    el.controls.hidden = true;
    el.transcript.hidden = true;
    el.quiz.hidden = true;
  }

  async function loadStory() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    const storyId = decodeURIComponent(parts[parts.length - 1] || "");
    if (!storyId || storyId === "listen") {
      fail("Story not found.");
      return;
    }

    setStatus("Loading story…");
    try {
      const data = await fetchJson("/api/stories/" + encodeURIComponent(storyId));
      state.story = data;
      state.answers = [];
      state.result = null;

      el.title.textContent = data.title || "";
      const meta = [];
      if (data.level !== null && data.level !== undefined) meta.push("HSK " + data.level);
      if (data.metrics && data.metrics.n_han) meta.push(data.metrics.n_han + " 字");
      if (data.seed_noun) meta.push(data.seed_noun);
      el.meta.textContent = meta.join(" · ");

      el.finished.checked = Boolean(data.finished);
      el.finished.disabled = false;
      el.empty.hidden = true;
      el.controls.hidden = false;
      renderTranscript();
      renderQuiz();

      document.title =
        (data.title || "Story") +
        (data.level !== null && data.level !== undefined ? " · HSK " + data.level : "");
      setStatus("");
    } catch (error) {
      fail("Could not load the story: " + error.message);
    }
  }

  el.finished.addEventListener("change", () => saveFinished(el.finished.checked));
  el.play.addEventListener("click", playStory);
  el.check.addEventListener("click", checkAnswers);
  el.transcriptToggle.addEventListener("click", () => {
    const showing = !el.transcript.hidden;
    el.transcript.hidden = showing;
    el.transcriptToggle.textContent = showing ? "Show text" : "Hide text";
  });
  window.addEventListener("beforeunload", () => {
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  });

  loadStory();
})();
