/* BrewSLM Academy — progress + quiz + lesson UX. No backend; localStorage only.
 * Content lives in HTML (SEO); this script only enhances it. */
(function () {
  "use strict";

  var KEY = "brewslm_academy_v1";

  /* ── storage with graceful degradation ──────────────────────────── */
  var memoryFallback = {};
  var storageOk = (function () {
    try {
      var t = "__a_probe__";
      window.localStorage.setItem(t, "1");
      window.localStorage.removeItem(t);
      return true;
    } catch (e) {
      return false;
    }
  })();

  function readRaw() {
    if (storageOk) return window.localStorage.getItem(KEY);
    return memoryFallback[KEY] || null;
  }
  function writeRaw(value) {
    if (storageOk) { window.localStorage.setItem(KEY, value); return; }
    memoryFallback[KEY] = value;
  }

  var AcademyProgress = {
    get: function () {
      try {
        var raw = readRaw();
        var parsed = raw ? JSON.parse(raw) : null;
        if (!parsed || typeof parsed !== "object") parsed = {};
        if (!parsed.lessons || typeof parsed.lessons !== "object") parsed.lessons = {};
        return parsed;
      } catch (e) {
        return { lessons: {} };
      }
    },
    _save: function (state) {
      state.updatedAt = Date.now();
      try { writeRaw(JSON.stringify(state)); } catch (e) { /* quota / private mode */ }
      return state;
    },
    isComplete: function (id) {
      var l = this.get().lessons[id];
      return !!(l && l.completed);
    },
    markComplete: function (id, value) {
      var state = this.get();
      var entry = state.lessons[id] || {};
      entry.completed = value !== false;
      entry.ts = Date.now();
      state.lessons[id] = entry;
      return this._save(state);
    },
    recordQuiz: function (id, score, total) {
      var state = this.get();
      var entry = state.lessons[id] || {};
      entry.quiz = { score: score, total: total, ts: Date.now() };
      state.lessons[id] = entry;
      return this._save(state);
    },
    reset: function () {
      if (storageOk) window.localStorage.removeItem(KEY); else delete memoryFallback[KEY];
    },
    exportJSON: function () {
      return JSON.stringify(this.get(), null, 2);
    },
    importJSON: function (text) {
      var parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || typeof parsed.lessons !== "object") {
        throw new Error("Not a valid Academy progress file.");
      }
      return this._save(parsed);
    },
    getName: function () {
      var n = this.get().name;
      return typeof n === "string" ? n : "";
    },
    setName: function (value) {
      var state = this.get();
      state.name = String(value || "").slice(0, 80);
      return this._save(state);
    },
  };
  window.AcademyProgress = AcademyProgress;

  /* ── degraded notice ─────────────────────────────────────────────── */
  function showDegradedNotice() {
    if (storageOk) return;
    var el = document.querySelector("[data-academy-degraded]");
    if (el) el.classList.add("is-shown");
  }

  /* ── lesson complete control ─────────────────────────────────────── */
  function initMarkComplete() {
    var btn = document.querySelector("[data-mark-complete]");
    var wrap = document.querySelector("[data-lesson-complete]");
    var lessonId = document.body.getAttribute("data-lesson-id");
    if (!btn || !lessonId) return;

    function render() {
      var done = AcademyProgress.isComplete(lessonId);
      btn.textContent = done ? "✓ Completed — mark incomplete" : "Mark lesson complete";
      btn.classList.toggle("btn-solid", !done);
      btn.classList.toggle("btn-outline", done);
      if (wrap) wrap.classList.toggle("is-complete", done);
      var status = document.querySelector("[data-complete-status]");
      if (status) {
        status.textContent = done
          ? "Saved to this browser. Your progress shows on the Academy hub."
          : "Progress is stored locally in your browser.";
      }
    }
    btn.addEventListener("click", function () {
      AcademyProgress.markComplete(lessonId, !AcademyProgress.isComplete(lessonId));
      render();
    });
    render();
  }

  /* ── table of contents + scrollspy ───────────────────────────────── */
  function slugify(text) {
    return text.toLowerCase().trim().replace(/[^a-z0-9\s-]/g, "").replace(/\s+/g, "-").slice(0, 60);
  }
  function initToc() {
    var tocList = document.querySelector("[data-toc]");
    var article = document.querySelector(".lesson-article");
    if (!tocList || !article) return;
    var headings = Array.prototype.slice.call(article.querySelectorAll("h2"));
    if (headings.length === 0) { var p = document.querySelector(".lesson-toc"); if (p) p.style.display = "none"; return; }
    var links = [];
    headings.forEach(function (h) {
      if (!h.id) h.id = slugify(h.textContent);
      var li = document.createElement("li");
      var a = document.createElement("a");
      a.href = "#" + h.id;
      a.textContent = h.textContent;
      li.appendChild(a);
      tocList.appendChild(li);
      links.push(a);
    });
    if ("IntersectionObserver" in window) {
      var spy = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var id = entry.target.id;
            links.forEach(function (a) { a.classList.toggle("is-active", a.getAttribute("href") === "#" + id); });
          }
        });
      }, { rootMargin: "-20% 0px -70% 0px" });
      headings.forEach(function (h) { spy.observe(h); });
    }
  }

  /* ── quiz engine ─────────────────────────────────────────────────── */
  function initQuizzes() {
    var blocks = Array.prototype.slice.call(document.querySelectorAll("[data-quiz]"));
    blocks.forEach(function (block) {
      var dataEl = block.querySelector('script[type="application/json"]');
      if (!dataEl) return;
      var questions;
      try { questions = JSON.parse(dataEl.textContent); } catch (e) { return; }
      if (!Array.isArray(questions) || questions.length === 0) return;
      var lessonId = document.body.getAttribute("data-lesson-id") || block.getAttribute("data-quiz-id") || "quiz";

      var form = document.createElement("form");
      form.className = "quiz__form";
      var state = questions.map(function () { return null; });

      questions.forEach(function (q, qi) {
        var qWrap = document.createElement("div");
        qWrap.className = "quiz__q";
        var qText = document.createElement("p");
        qText.className = "quiz__q-text";
        qText.textContent = (qi + 1) + ". " + q.q;
        qWrap.appendChild(qText);

        var opts = document.createElement("div");
        opts.className = "quiz__options";
        (q.options || []).forEach(function (optText, oi) {
          var label = document.createElement("label");
          label.className = "quiz__option";
          var input = document.createElement("input");
          input.type = "radio";
          input.name = "q" + qi;
          input.value = String(oi);
          input.addEventListener("change", function () { state[qi] = oi; });
          var span = document.createElement("span");
          span.textContent = optText;
          label.appendChild(input);
          label.appendChild(span);
          opts.appendChild(label);
        });
        qWrap.appendChild(opts);

        var explain = document.createElement("div");
        explain.className = "quiz__explanation";
        explain.textContent = q.explanation || "";
        qWrap.appendChild(explain);

        form.appendChild(qWrap);
      });

      var submit = document.createElement("button");
      submit.type = "submit";
      submit.className = "btn btn-solid";
      submit.textContent = "Check answers";
      form.appendChild(submit);

      var result = document.createElement("p");
      result.className = "quiz__result";
      form.appendChild(result);

      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var score = 0;
        var qWraps = form.querySelectorAll(".quiz__q");
        questions.forEach(function (q, qi) {
          var chosen = state[qi];
          var labels = qWraps[qi].querySelectorAll(".quiz__option");
          labels.forEach(function (l) { l.classList.remove("is-correct", "is-wrong"); });
          if (chosen === q.answer) {
            score++;
            if (labels[chosen]) labels[chosen].classList.add("is-correct");
          } else {
            if (chosen != null && labels[chosen]) labels[chosen].classList.add("is-wrong");
            if (labels[q.answer]) labels[q.answer].classList.add("is-correct");
          }
          var exp = qWraps[qi].querySelector(".quiz__explanation");
          if (exp) exp.classList.add("is-shown");
        });
        var pass = score === questions.length;
        result.textContent = "You scored " + score + " / " + questions.length +
          (pass ? " — perfect." : " — review the explanations and try again.");
        result.classList.toggle("is-pass", pass);
        AcademyProgress.recordQuiz(lessonId, score, questions.length);
      });

      block.appendChild(form);
    });
  }

  /* ── hub progress dashboard ──────────────────────────────────────── */
  /* ── completion certificate ──────────────────────────────────────── */
  function initCertificate() {
    var root = document.querySelector("[data-certificate]");
    if (!root) return;

    // manifest of all lessons (denominator), embedded in the page
    var manifest = [];
    var mEl = root.querySelector('script[type="application/json"][data-academy-manifest]');
    if (mEl) { try { manifest = JSON.parse(mEl.textContent) || []; } catch (e) { manifest = []; } }

    var total = manifest.length;
    var done = manifest.filter(function (m) { return AcademyProgress.isComplete(m.id); });
    var doneCount = done.length;
    var earned = total > 0 && doneCount === total;

    // progress numbers
    var pctEl = root.querySelector("[data-cert-pct]");
    if (pctEl) pctEl.textContent = (total ? Math.round((doneCount / total) * 100) : 0) + "%";
    var countEl = root.querySelector("[data-cert-count]");
    if (countEl) countEl.textContent = doneCount + " of " + total;
    var fill = root.querySelector("[data-cert-fill]");
    if (fill) fill.style.width = (total ? Math.round((doneCount / total) * 100) : 0) + "%";

    var earnedBox = root.querySelector("[data-cert-earned]");
    var lockedBox = root.querySelector("[data-cert-locked]");

    if (earned) {
      if (earnedBox) earnedBox.classList.remove("is-hidden");
      if (lockedBox) lockedBox.classList.add("is-hidden");

      // name binding
      var nameInput = root.querySelector("[data-cert-name]");
      var nameOut = root.querySelector("[data-cert-name-out]");
      function paintName() {
        var n = (nameInput && nameInput.value.trim()) || AcademyProgress.getName() || "Anonymous learner";
        if (nameOut) nameOut.textContent = n;
      }
      if (nameInput) {
        nameInput.value = AcademyProgress.getName();
        nameInput.addEventListener("input", function () {
          AcademyProgress.setName(nameInput.value.trim());
          paintName();
        });
      }
      paintName();

      // completion date — latest lesson timestamp, else today
      var latest = 0;
      var lessons = AcademyProgress.get().lessons || {};
      Object.keys(lessons).forEach(function (k) {
        var ts = lessons[k] && lessons[k].ts;
        if (typeof ts === "number" && ts > latest) latest = ts;
      });
      var dateEl = root.querySelector("[data-cert-date]");
      if (dateEl) {
        var d = latest ? new Date(latest) : new Date();
        dateEl.textContent = d.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
      }

      var printBtn = root.querySelector("[data-cert-print]");
      if (printBtn) printBtn.addEventListener("click", function () { window.print(); });
    } else {
      if (earnedBox) earnedBox.classList.add("is-hidden");
      if (lockedBox) lockedBox.classList.remove("is-hidden");

      // list what's left, grouped by track
      var remaining = manifest.filter(function (m) { return !AcademyProgress.isComplete(m.id); });
      var listEl = root.querySelector("[data-cert-remaining]");
      if (listEl) {
        listEl.innerHTML = "";
        remaining.forEach(function (m) {
          var li = document.createElement("li");
          var a = document.createElement("a");
          a.className = "inline-link";
          a.href = m.url;
          a.textContent = (m.track ? m.track + " · " : "") + m.title;
          li.appendChild(a);
          listEl.appendChild(li);
        });
      }
      var remCount = root.querySelector("[data-cert-remaining-count]");
      if (remCount) remCount.textContent = remaining.length;
    }
  }

  function initHub() {
    var hub = document.querySelector("[data-academy-hub]");
    if (!hub) return;

    var allLessons = [];
    var firstIncomplete = null;

    var cards = Array.prototype.slice.call(hub.querySelectorAll("[data-track]"));
    cards.forEach(function (card) {
      var items = Array.prototype.slice.call(card.querySelectorAll("[data-lesson]"));
      var done = 0;
      items.forEach(function (li) {
        var id = li.getAttribute("data-lesson");
        var available = li.getAttribute("data-available") === "true";
        var complete = AcademyProgress.isComplete(id);
        if (complete) { li.classList.add("is-done"); done++; }
        allLessons.push({ id: id, available: available, complete: complete, url: li.getAttribute("data-url") });
        if (!firstIncomplete && available && !complete) {
          firstIncomplete = { url: li.getAttribute("data-url"), title: (li.textContent || "").trim() };
        }
      });
      var total = items.length;
      var pct = total ? Math.round((done / total) * 100) : 0;
      var fill = card.querySelector(".progress-bar__fill");
      if (fill) fill.style.width = pct + "%";
      var meta = card.querySelector("[data-track-count]");
      if (meta) meta.textContent = done + " / " + total + " complete";
    });

    var totalLessons = allLessons.length;
    var doneLessons = allLessons.filter(function (l) { return l.complete; }).length;
    var overall = totalLessons ? Math.round((doneLessons / totalLessons) * 100) : 0;
    var pctEl = hub.querySelector("[data-overall-pct]");
    if (pctEl) pctEl.textContent = overall + "%";
    var overallFill = hub.querySelector("[data-overall-fill]");
    if (overallFill) overallFill.style.width = overall + "%";
    var countEl = hub.querySelector("[data-overall-count]");
    if (countEl) countEl.textContent = doneLessons + " of " + totalLessons + " lessons";

    var cont = hub.querySelector("[data-continue]");
    if (cont) {
      var target = firstIncomplete ||
        (allLessons.find(function (l) { return l.available; }) || null);
      if (target && target.url) {
        cont.href = target.url;
        cont.classList.remove("is-hidden");
        var label = cont.querySelector("[data-continue-label]");
        if (label && firstIncomplete) label.textContent = firstIncomplete.title;
      } else {
        cont.classList.add("is-hidden");
      }
    }

    // export / import / reset controls
    var exportBtn = hub.querySelector("[data-export]");
    if (exportBtn) exportBtn.addEventListener("click", function () {
      var blob = new Blob([AcademyProgress.exportJSON()], { type: "application/json" });
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url; a.download = "brewslm-academy-progress.json"; a.click();
      URL.revokeObjectURL(url);
    });
    var importInput = hub.querySelector("[data-import]");
    if (importInput) importInput.addEventListener("change", function (e) {
      var file = e.target.files && e.target.files[0];
      if (!file) return;
      var reader = new FileReader();
      reader.onload = function () {
        try { AcademyProgress.importJSON(String(reader.result)); window.location.reload(); }
        catch (err) { alert("Could not import: " + err.message); }
      };
      reader.readAsText(file);
    });
    var resetBtn = hub.querySelector("[data-reset]");
    if (resetBtn) resetBtn.addEventListener("click", function () {
      if (window.confirm("Reset all Academy progress in this browser?")) {
        AcademyProgress.reset(); window.location.reload();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    showDegradedNotice();
    initMarkComplete();
    initToc();
    initQuizzes();
    initHub();
    initCertificate();
  });
})();
