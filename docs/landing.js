(function () {
  "use strict";

  var ctaLinks = Array.prototype.slice.call(document.querySelectorAll(".js-bot-cta"));
  var statusEl = document.querySelector(".js-bot-status");

  function setStatus(text) {
    if (statusEl) {
      statusEl.textContent = text;
    }
  }

  function enableCtas(botUsername) {
    var cleanUsername = String(botUsername || "").replace(/^@+/, "").trim();
    if (!cleanUsername) {
      return false;
    }

    var botUrl = "https://t.me/" + encodeURIComponent(cleanUsername) + "?start=landing";
    ctaLinks.forEach(function (link) {
      link.href = botUrl;
      link.removeAttribute("aria-disabled");
      link.setAttribute("target", "_blank");
      link.setAttribute("rel", "noopener noreferrer");
    });
    setStatus("Откроется Telegram-бот @" + cleanUsername + ".");
    return true;
  }

  function showFallback() {
    ctaLinks.forEach(function (link) {
      link.href = "/webapp/";
      link.removeAttribute("aria-disabled");
    });
    setStatus("Если Telegram не открылся, зайдите в мини-приложение и откройте бота оттуда.");
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function initFeatureStack() {
    var stories = Array.prototype.slice.call(document.querySelectorAll(".feature-story"));
    var reduceMotion = window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var ticking = false;

    if (!stories.length || reduceMotion) {
      return;
    }

    function resetStories() {
      stories.forEach(function (story) {
        story.style.removeProperty("--stack-scale");
        story.style.removeProperty("--stack-shift");
      });
    }

    function updateStack() {
      ticking = false;

      var isMobile = window.innerWidth <= 860;

      stories.forEach(function (story, index) {
        var nextStory = stories[index + 1];

        if (!nextStory) {
          story.style.setProperty("--stack-scale", "1");
          story.style.setProperty("--stack-shift", "0px");
          return;
        }

        var storyRect = story.getBoundingClientRect();
        var nextTop = nextStory.getBoundingClientRect().top;
        var overlap = storyRect.bottom - nextTop;
        var startOverlap = storyRect.height * 0.05;
        var endOverlap = storyRect.height * (isMobile ? 0.58 : 0.72);
        var progress = clamp((overlap - startOverlap) / (endOverlap - startOverlap), 0, 1);
        var scale = 1 - progress * (isMobile ? 0.045 : 0.075);
        var shift = progress * (isMobile ? 18 : 30);

        story.style.setProperty("--stack-scale", scale.toFixed(3));
        story.style.setProperty("--stack-shift", shift.toFixed(1) + "px");
      });
    }

    function requestUpdate() {
      if (!ticking) {
        ticking = true;
        window.requestAnimationFrame(updateStack);
      }
    }

    updateStack();
    window.addEventListener("scroll", requestUpdate, { passive: true });
    window.addEventListener("resize", requestUpdate);
  }

  initFeatureStack();

  if (!ctaLinks.length || !window.fetch) {
    showFallback();
    return;
  }

  window.fetch("/api/miniapp/auth/config", { credentials: "same-origin" })
    .then(function (response) {
      if (!response.ok) {
        throw new Error("config request failed");
      }
      return response.json();
    })
    .then(function (config) {
      if (!enableCtas(config && config.bot_username)) {
        showFallback();
      }
    })
    .catch(function () {
      showFallback();
    });
})();
