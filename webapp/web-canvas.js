/**
 * Leo web-canvas: sandboxed iframe preview + element picker for GPT edits.
 * Exposes window.LeoWebCanvas.
 */
(function (global) {
  "use strict";

  var PICKER_SCRIPT =
    "(function(){" +
    "var pick=false;var selected=null;" +
    "function post(type,payload){try{parent.postMessage(Object.assign({source:'leo-web-canvas',type:type},payload||{}), '*');}catch(e){}}" +
    "function clearSel(){if(selected){selected.classList.remove('leo-selected');selected=null;}}" +
    "function onMove(e){if(!pick)return;}" +
    "function onClick(e){" +
    "if(!pick)return;" +
    "e.preventDefault();e.stopPropagation();" +
    "var el=e.target;if(!el||el===document.documentElement||el===document.body)return;" +
    "var node=el.closest('[data-leo-id]')||el;" +
    "clearSel();selected=node;node.classList.add('leo-selected');" +
    "var id=node.getAttribute('data-leo-id')||'';" +
    "var label=(node.innerText||'').trim().slice(0,80);" +
    "var snippet=(node.outerHTML||'').slice(0,4000);" +
    "var rect=node.getBoundingClientRect();" +
    "post('select',{leoId:id,label:label,snippet:snippet,tag:node.tagName,rect:{t:rect.top,l:rect.left,w:rect.width,h:rect.height}});" +
    "}" +
    "window.addEventListener('message',function(ev){" +
    "var d=ev&&ev.data;if(!d||d.source!=='leo-web-canvas-host')return;" +
    "if(d.type==='pick'){pick=!!d.on;document.documentElement.classList.toggle('leo-pick-mode',pick);if(!pick)clearSel();}" +
    "if(d.type==='highlight'){clearSel();var n=d.leoId&&document.querySelector('[data-leo-id=\"'+String(d.leoId).replace(/\"/g,'')+'\"]');if(n){selected=n;n.classList.add('leo-selected');}}" +
    "if(d.type==='clear'){clearSel();}" +
    "});" +
    "document.addEventListener('click',onClick,true);" +
    "document.addEventListener('mousemove',onMove,true);" +
    "post('ready',{});" +
    "})();";

  function injectPicker(html) {
    var raw = String(html || "");
    var tag =
      '<script id="leo-canvas-picker">\n' + PICKER_SCRIPT + "\n</script>";
    if (/<\/body>/i.test(raw)) {
      return raw.replace(/<\/body>/i, tag + "</body>");
    }
    return raw + tag;
  }

  function create(opts) {
    opts = opts || {};
    var host = opts.host;
    if (!host) throw new Error("LeoWebCanvas: host required");
    var onSelect = typeof opts.onSelect === "function" ? opts.onSelect : null;
    var onRefresh = typeof opts.onRefresh === "function" ? opts.onRefresh : null;
    var sheetId = String(opts.sheetId || "");
    var wrap = document.createElement("div");
    wrap.className = "note-web-canvas";
    var toolbar = document.createElement("div");
    toolbar.className = "note-web-canvas-toolbar";
    var pickBtn = document.createElement("button");
    pickBtn.type = "button";
    pickBtn.className = "note-web-canvas-pick-btn";
    pickBtn.textContent = "Выделить";
    toolbar.appendChild(pickBtn);
    var refreshBtn = document.createElement("button");
    refreshBtn.type = "button";
    refreshBtn.className = "note-web-canvas-pick-btn note-web-canvas-refresh-btn";
    refreshBtn.textContent = "Обновить";
    refreshBtn.title = "Перезагрузить полотно";
    toolbar.appendChild(refreshBtn);
    var frame = document.createElement("iframe");
    frame.className = "note-web-canvas-frame";
    // Scripts for our picker only; no same-origin so page JS cannot touch parent.
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("title", "Web canvas");
    wrap.appendChild(toolbar);
    wrap.appendChild(frame);
    host.innerHTML = "";
    host.appendChild(wrap);

    var picking = false;
    var htmlDoc = "";
    var ready = false;

    function postToFrame(msg) {
      try {
        if (frame.contentWindow) {
          frame.contentWindow.postMessage(
            Object.assign({ source: "leo-web-canvas-host" }, msg),
            "*"
          );
        }
      } catch (e) {}
    }

    function setHtml(html) {
      htmlDoc = String(html || "");
      ready = false;
      frame.srcdoc = injectPicker(htmlDoc);
    }

    function getHtml() {
      return htmlDoc;
    }

    function setPick(on) {
      picking = !!on;
      pickBtn.classList.toggle("is-active", picking);
      pickBtn.textContent = picking ? "Отмена" : "Выделить";
      postToFrame({ type: "pick", on: picking });
    }

    pickBtn.addEventListener("click", function (e) {
      e.preventDefault();
      setPick(!picking);
    });

    refreshBtn.addEventListener("click", function (e) {
      e.preventDefault();
      if (refreshBtn.disabled) return;
      refreshBtn.disabled = true;
      refreshBtn.textContent = "…";
      Promise.resolve()
        .then(function () {
          return onRefresh ? onRefresh() : null;
        })
        .catch(function () {})
        .then(function () {
          refreshBtn.disabled = false;
          refreshBtn.textContent = "Обновить";
        });
    });

    function onMessage(ev) {
      var d = ev && ev.data;
      if (!d || d.source !== "leo-web-canvas") return;
      if (frame.contentWindow && ev.source !== frame.contentWindow) return;
      if (d.type === "ready") {
        ready = true;
        if (picking) postToFrame({ type: "pick", on: true });
        return;
      }
      if (d.type === "select" && onSelect) {
        onSelect({
          sheetId: sheetId,
          leoId: String(d.leoId || ""),
          label: String(d.label || ""),
          snippet: String(d.snippet || ""),
          tag: String(d.tag || ""),
        });
        setPick(false);
      }
    }
    window.addEventListener("message", onMessage);

    setHtml(opts.html || "");

    return {
      setHtml: setHtml,
      getHtml: getHtml,
      setSheetId: function (id) {
        sheetId = String(id || "");
      },
      setPick: setPick,
      highlight: function (leoId) {
        postToFrame({ type: "highlight", leoId: leoId });
      },
      destroy: function () {
        window.removeEventListener("message", onMessage);
        try {
          host.innerHTML = "";
        } catch (e) {}
      },
      el: wrap,
      frame: frame,
    };
  }

  global.LeoWebCanvas = { create: create, injectPicker: injectPicker };
})(typeof window !== "undefined" ? window : globalThis);
