/**
 * Leo web-canvas: sandboxed iframe preview, GPT pick, and manual edit mode.
 * Exposes window.LeoWebCanvas.
 */
(function (global) {
  "use strict";

  var FRAME_SCRIPT =
    "(function(){" +
    "var pick=false,edit=false,selected=null,dragState=null;" +
    // Template decks: only slot nodes are editable text; layout comes from the server.
    "var TPL=document.documentElement.hasAttribute('data-leo-deck');" +
    "var TEXT_SEL=TPL?'[data-slot]':'.title,.display,.body,.lead,.caption,.pill,.metric-xl,.tile-num,h1,h2,h3,p,span,blockquote';" +
    "var MEDIA_SEL='.abstract,.media-slot,img.media-img,.pane-stage';" +
    "function post(type,payload){try{parent.postMessage(Object.assign({source:'leo-web-canvas',type:type},payload||{}), '*');}catch(e){}}" +
    "function uid(prefix){return String(prefix||'el')+'-'+Math.random().toString(36).slice(2,8);}" +
    "function clearSel(){if(selected){selected.classList.remove('leo-selected','leo-edit-selected');selected=null;}document.querySelectorAll('.leo-drop-target').forEach(function(n){n.classList.remove('leo-drop-target');});}" +
    "function stripEditChrome(root){" +
    "root=root||document.documentElement;" +
    "root.classList.remove('leo-pick-mode','leo-edit-mode','leo-review');" +
    "root.querySelectorAll('[contenteditable]').forEach(function(n){n.removeAttribute('contenteditable');});" +
    "root.querySelectorAll('.leo-selected,.leo-edit-selected,.leo-drop-target,.leo-dragging').forEach(function(n){n.classList.remove('leo-selected','leo-edit-selected','leo-drop-target','leo-dragging');});" +
    "root.querySelectorAll('.leo-edit-chrome').forEach(function(n){n.remove();});" +
    "}" +
    "function snapshotHtml(){" +
    "var clone=document.documentElement.cloneNode(true);" +
    "stripEditChrome(clone);" +
    "var picker=clone.querySelector('#leo-canvas-picker');if(picker)picker.remove();" +
    "var html='<!DOCTYPE html>'+clone.outerHTML;" +
    "post('html',{html:html});" +
    "return html;" +
    "}" +
    "function enableTextEdit(on){" +
    "document.querySelectorAll(TEXT_SEL).forEach(function(n){" +
    "if(!on){n.removeAttribute('contenteditable');return;}" +
    "if(n.closest('svg'))return;" +
    "n.setAttribute('contenteditable','true');" +
    "});" +
    "}" +
    "function isMediaNode(n){return !!(n&&(n.matches&&n.matches(MEDIA_SEL)||n.hasAttribute&&n.hasAttribute('data-a3')||n.classList&&(n.classList.contains('abstract')||n.classList.contains('media-slot')||n.classList.contains('media-img'))));}" +
    "function ensureLeoId(n){if(!n)return'';var id=n.getAttribute('data-leo-id');if(!id){id=uid('block');n.setAttribute('data-leo-id',id);}return id;}" +
    "function showEditChrome(node){" +
    "document.querySelectorAll('.leo-edit-chrome:not(.leo-review-ui)').forEach(function(n){n.remove();});" +
    "if(!node||!edit)return;" +
    "if(TPL&&isMediaNode(node))return;" +
    "var bar=document.createElement('div');" +
    "bar.className='leo-edit-chrome';" +
    "var html='<button type=\"button\" data-act=\"drag\" class=\"leo-drag-handle\" title=\"Перетащить\">⠿</button>';" +
    "if(isMediaNode(node)){" +
    "html+='<button type=\"button\" data-act=\"upload\">Загрузить</button><button type=\"button\" data-act=\"generate\">Сгенерировать</button><button type=\"button\" data-act=\"clear\">Убрать</button>';" +
    "}" +
    "html+='<button type=\"button\" data-act=\"dup\" title=\"Дублировать\">⧉</button><button type=\"button\" data-act=\"del\" title=\"Удалить\">✕</button>';" +
    "bar.innerHTML=html;" +
    "bar.addEventListener('mousedown',function(e){" +
    "var btn=e.target&&e.target.closest&&e.target.closest('button[data-act]');" +
    "if(btn&&btn.getAttribute('data-act')==='drag'){startDrag(e,node);return;}" +
    "e.preventDefault();e.stopPropagation();" +
    "});" +
    "bar.addEventListener('click',function(e){" +
    "e.preventDefault();e.stopPropagation();" +
    "var btn=e.target&&e.target.closest&&e.target.closest('button[data-act]');" +
    "if(!btn)return;" +
    "var act=btn.getAttribute('data-act');" +
    "if(act==='drag')return;" +
    "var leoId=ensureLeoId(node);" +
    "if(act==='del'){deleteNode(node);return;}" +
    "if(act==='dup'){duplicateNode(node);return;}" +
    "if(act==='upload'||act==='generate'||act==='clear'){post('media',{action:act,leoId:leoId});}" +
    "});" +
    "var cs=window.getComputedStyle(node);" +
    "if(cs.position==='static')node.style.position='relative';" +
    "node.appendChild(bar);" +
    "}" +
    "function selectNode(node){" +
    "clearSel();" +
    "if(!node)return;" +
    "selected=node;" +
    "ensureLeoId(node);" +
    "node.classList.add(edit?'leo-edit-selected':'leo-selected');" +
    "if(edit)showEditChrome(node);" +
    "post('focus',{leoId:node.getAttribute('data-leo-id')||'',isMedia:isMediaNode(node)});" +
    "}" +
    "function deleteNode(node){" +
    "if(!node||node.classList&&node.classList.contains('deck'))return;" +
    "var parent=node.parentNode;" +
    "var next=node.nextElementSibling||node.previousElementSibling||parent;" +
    "node.remove();" +
    "clearSel();" +
    "if(next&&next!==document.body&&next!==document.documentElement)selectNode(next);" +
    "snapshotHtml();" +
    "}" +
    "function duplicateNode(node){" +
    "if(!node||!node.parentNode)return;" +
    "var clone=node.cloneNode(true);" +
    "clone.querySelectorAll('.leo-edit-chrome').forEach(function(n){n.remove();});" +
    "clone.classList.remove('leo-edit-selected','leo-selected','leo-dragging','leo-drop-target');" +
    "clone.querySelectorAll('[data-leo-id]').forEach(function(n){n.setAttribute('data-leo-id',uid('block'));});" +
    "clone.setAttribute('data-leo-id',uid('block'));" +
    "node.parentNode.insertBefore(clone,node.nextSibling);" +
    "if(edit)enableTextEdit(true);" +
    "selectNode(clone);" +
    "snapshotHtml();" +
    "}" +
    "function currentSlide(){" +
    "if(selected){var s=selected.closest&&selected.closest('.slide');if(s)return s;}" +
    "return document.querySelector('.slide')||document.querySelector('.deck')||document.body;" +
    "}" +
    "function insertTarget(){" +
    "var slide=currentSlide();" +
    "if(selected&&selected!==slide&&slide.contains(selected)){" +
    "var p=selected.parentNode;" +
    "if(p&&p!==document.body)return{parent:p,after:selected};" +
    "}" +
    "var copy=slide.querySelector&&slide.querySelector('.pane-copy');" +
    "if(copy)return{parent:copy,after:null};" +
    "var deck=slide.querySelector&&slide.querySelector('.deck');" +
    "return{parent:slide,after:null};" +
    "}" +
    "function makeBlock(kind){" +
    "var el;" +
    "if(kind==='heading'){el=document.createElement('h2');el.className='title';el.textContent='Заголовок';}" +
    "else if(kind==='display'){el=document.createElement('h1');el.className='display';el.textContent='Крупный заголовок';}" +
    "else if(kind==='text'){el=document.createElement('p');el.className='body';el.textContent='Новый текст';}" +
    "else if(kind==='lead'){el=document.createElement('p');el.className='lead';el.textContent='Лид';}" +
    "else if(kind==='caption'){el=document.createElement('p');el.className='caption';el.textContent='Подпись';}" +
    "else if(kind==='pill'){el=document.createElement('p');el.className='pill pill-ink';el.textContent='метка';}" +
    "else if(kind==='metric'){el=document.createElement('div');el.className='tile';el.innerHTML='<p class=\"metric-xl\">100</p><p class=\"caption\">метка</p>';}" +
    "else if(kind==='image'){el=document.createElement('div');el.className='media-slot abstract abstract-orb';el.setAttribute('aria-hidden','true');}" +
    "else if(kind==='slide'){" +
    "el=document.createElement('section');el.className='slide';" +
    "el.innerHTML='<div class=\"slide-head\"><h2 class=\"title\">Новый слайд</h2></div><p class=\"body\">Текст слайда</p>';" +
    "}" +
    "else{el=document.createElement('p');el.className='body';el.textContent='Новый текст';}" +
    "ensureLeoId(el);" +
    "el.querySelectorAll('h1,h2,h3,p,span,.title,.display,.body,.lead,.caption,.pill,.metric-xl,.tile-num').forEach(function(n){ensureLeoId(n);});" +
    "return el;" +
    "}" +
    "function insertBlock(kind){" +
    "if(!edit)return;" +
    "var el=makeBlock(kind);" +
    "if(kind==='slide'){" +
    "var deck=document.querySelector('.deck')||document.body;" +
    "var cur=currentSlide();" +
    "if(cur&&cur.parentNode===deck)deck.insertBefore(el,cur.nextSibling);else deck.appendChild(el);" +
    "}else{" +
    "var t=insertTarget();" +
    "if(t.after&&t.after.parentNode===t.parent)t.parent.insertBefore(el,t.after.nextSibling);" +
    "else t.parent.appendChild(el);" +
    "}" +
    "if(edit)enableTextEdit(true);" +
    "selectNode(el);" +
    "try{el.scrollIntoView({block:'nearest',behavior:'smooth'});}catch(err){}" +
    "snapshotHtml();" +
    "}" +
    "function startDrag(e,node){" +
    "if(!edit||!node||!node.parentNode)return;" +
    "e.preventDefault();e.stopPropagation();" +
    "dragState={el:node,parent:node.parentNode,startY:e.clientY,moved:false};" +
    "node.classList.add('leo-dragging');" +
    "document.addEventListener('mousemove',onDragMove,true);" +
    "document.addEventListener('mouseup',onDragUp,true);" +
    "}" +
    "function siblingBlocks(parent,exclude){" +
    "return Array.prototype.filter.call(parent.children||[],function(n){" +
    "return n!==exclude&&n.nodeType===1&&!n.classList.contains('leo-edit-chrome');" +
    "});" +
    "}" +
    "function onDragMove(e){" +
    "if(!dragState)return;" +
    "e.preventDefault();" +
    "dragState.moved=true;" +
    "var parent=dragState.parent;" +
    "var y=e.clientY;" +
    "var siblings=siblingBlocks(parent,dragState.el);" +
    "document.querySelectorAll('.leo-drop-target').forEach(function(n){n.classList.remove('leo-drop-target');});" +
    "var target=null,before=true;" +
    "for(var i=0;i<siblings.length;i++){" +
    "var r=siblings[i].getBoundingClientRect();" +
    "var mid=r.top+r.height/2;" +
    "if(y<mid){target=siblings[i];before=true;break;}" +
    "target=siblings[i];before=false;" +
    "}" +
    "if(!target)return;" +
    "target.classList.add('leo-drop-target');" +
    "if(before)parent.insertBefore(dragState.el,target);" +
    "else parent.insertBefore(dragState.el,target.nextSibling);" +
    "}" +
    "function onDragUp(e){" +
    "if(!dragState)return;" +
    "e.preventDefault();e.stopPropagation();" +
    "document.removeEventListener('mousemove',onDragMove,true);" +
    "document.removeEventListener('mouseup',onDragUp,true);" +
    "var el=dragState.el;" +
    "var moved=dragState.moved;" +
    "el.classList.remove('leo-dragging');" +
    "document.querySelectorAll('.leo-drop-target').forEach(function(n){n.classList.remove('leo-drop-target');});" +
    "dragState=null;" +
    "if(moved){selectNode(el);snapshotHtml();}" +
    "}" +
    "function onClick(e){" +
    "if(!pick&&!edit)return;" +
    "if(dragState)return;" +
    "if(e.target&&e.target.closest&&e.target.closest('.leo-edit-chrome'))return;" +
    "var el=e.target;if(!el||el===document.documentElement||el===document.body)return;" +
    "if(edit&&TPL){var media=el.closest('[data-media]');if(!media)return;e.preventDefault();e.stopPropagation();selectNode(media);openMediaMenu(media);return;}" +
    "e.preventDefault();e.stopPropagation();" +
    "var node=el.closest('[data-leo-id]')||el;" +
    "if(pick){" +
    "selectNode(node);" +
    "post('select',{leoId:node.getAttribute('data-leo-id')||'',label:(node.innerText||'').trim().slice(0,80),snippet:(node.outerHTML||'').slice(0,4000),tag:node.tagName});" +
    "return;" +
    "}" +
    "if(edit)selectNode(node);" +
    "}" +
    "function openMediaMenu(m){" +
    "var sl=m.closest('[data-slide-id]');var t=sl&&sl.querySelector('.title,.display,h1,h2');" +
    "post('media',{action:'menu',leoId:ensureLeoId(m)," +
    "prompt:m.getAttribute('data-leo-img-prompt')||m.getAttribute('data-img-prompt')||''," +
    "hasImg:!!m.querySelector('img.media-img'),isVisual:m.hasAttribute('data-a3')," +
    "slideTitle:t?String(t.textContent||'').trim().slice(0,120):''});" +
    "}" +
    "function onInput(){if(edit)post('dirty',{});}" +
    "function applyImg(leoId,dataUrl,prompt){" +
    "var n=leoId&&document.querySelector('[data-leo-id=\"'+String(leoId).replace(/\"/g,'')+'\"]');" +
    "if(!n||!dataUrl)return;" +
    "var img=document.createElement('img');" +
    "img.className='media-img';" +
    "img.setAttribute('data-leo-id',leoId);" +
    "img.alt='';" +
    "img.src=dataUrl;" +
    "if(n.tagName&&n.tagName.toLowerCase()==='img'){n.src=dataUrl;n.classList.add('media-img');}" +
    "else if(n.hasAttribute('data-a3')){n.innerHTML='';n.classList.add('a3-photo');n.appendChild(img);}" +
    "else{n.innerHTML='';n.classList.add('media-slot');n.appendChild(img);}" +
    "if(n.tagName.toLowerCase()!=='img'){n.removeAttribute('data-leo-img');n.removeAttribute('data-leo-img-prompt');n.classList.remove('tpl-media-empty');" +
    "if(prompt)n.setAttribute('data-img-prompt',String(prompt).slice(0,600));else n.removeAttribute('data-img-prompt');}" +
    "selectNode(n.tagName.toLowerCase()==='img'?n:n);" +
    "snapshotHtml();" +
    "}" +
    "function clearMedia(leoId){" +
    "var n=leoId&&document.querySelector('[data-leo-id=\"'+String(leoId).replace(/\"/g,'')+'\"]');" +
    "if(!n)return;" +
    "if(n.hasAttribute('data-a3')){var v=n.getAttribute('data-a3')||'orb';n.classList.remove('a3-photo');" +
    "n.innerHTML='<div class=\"a3-'+v.replace(/[^a-z]/g,'')+'\">'+(v==='cube'?'<i></i><i></i><i></i>':'')+'</div>';selectNode(n);snapshotHtml();return;}" +
    "if(n.tagName&&n.tagName.toLowerCase()==='img'){" +
    "var wrap=document.createElement('div');" +
    "wrap.className='abstract abstract-orb media-slot';" +
    "wrap.setAttribute('data-leo-id',leoId);" +
    "wrap.setAttribute('aria-hidden','true');" +
    "n.replaceWith(wrap);selectNode(wrap);" +
    "}else{" +
    "n.innerHTML='';n.className=(n.className||'').replace(/\\bmedia-img\\b/g,'').trim();" +
    "if(!/\\babstract\\b/.test(n.className))n.className=(n.className+' abstract abstract-orb media-slot').trim();" +
    "selectNode(n);" +
    "}" +
    "snapshotHtml();" +
    "}" +
    "window.addEventListener('message',function(ev){" +
    "var d=ev&&ev.data;if(!d||d.source!=='leo-web-canvas-host')return;" +
    "if(d.type==='pick'){edit=false;pick=!!d.on;stripEditChrome();document.documentElement.classList.toggle('leo-pick-mode',pick);enableTextEdit(false);if(!pick)clearSel();}" +
    "if(d.type==='edit'){pick=false;edit=!!d.on;stripEditChrome();document.documentElement.classList.toggle('leo-edit-mode',edit);enableTextEdit(edit);if(!edit){clearSel();snapshotHtml();}else clearSel();}" +
    "if(d.type==='highlight'){clearSel();var n=d.leoId&&document.querySelector('[data-leo-id=\"'+String(d.leoId).replace(/\"/g,'')+'\"]');if(n){selected=n;n.classList.add(edit?'leo-edit-selected':'leo-selected');if(edit)showEditChrome(n);}}" +
    "if(d.type==='clear'){clearSel();}" +
    "if(d.type==='getHtml'){snapshotHtml();}" +
    "if(d.type==='setImg'){applyImg(d.leoId,d.dataUrl,d.prompt||'');}" +
    "if(d.type==='clearMedia'){clearMedia(d.leoId);}" +
    "if(d.type==='insert'){insertBlock(String(d.kind||'text'));}" +
    "if(d.type==='deleteSelected'){if(selected)deleteNode(selected);}" +
    "});" +
    "document.addEventListener('click',onClick,true);" +
    "document.addEventListener('input',onInput,true);" +
    "post('ready',{});" +
    "})();";

  /**
   * Edit-mode review UI inside the frame (serialized into srcdoc).
   * Every slide gets a comment box below it; template decks also get a layout
   * menu (chevron, top-right), slide move/delete, and add/remove for repeated items.
   * All nodes carry .leo-edit-chrome so snapshots strip them.
   */
  function frameReviewMain() {
    var root = document.documentElement;
    var TPL = root.hasAttribute("data-leo-deck");
    var on = false;
    var comments = {};
    var statuses = {};
    var variantsUi = {};
    var layouts = []; // «Мои макеты» пользователя
    var LAYOUT_HINTS = {
      auto: "Что будет на новом слайде? Можно оставить пустым — ИИ продолжит тему по заметке",
      custom: "Опишите раскладку: например, слева большая цифра, справа три карточки, внизу цитата",
      saved: "О чём слайд? ИИ заполнит сохранённый макет текстом из заметки",
    };
    var menu = null;
    var hover = null;
    var hoverItem = null;

    function post(type, payload) {
      if (type === "op") payload = Object.assign({ scrollY: window.scrollY || 0 }, payload);
      try {
        parent.postMessage(Object.assign({ source: "leo-web-canvas", type: type }, payload || {}), "*");
      } catch (e) {}
    }

    function readUi() {
      variantsUi = {};
      try {
        var el = document.getElementById("leo-deck");
        var data = el ? JSON.parse(el.textContent || "{}") : {};
        variantsUi = (data.ui && data.ui.variants) || {};
      } catch (e) {}
    }

    function slides() {
      return [].slice.call(document.querySelectorAll("section.slide")).filter(function (s) {
        return !s.parentNode.closest || !s.parentNode.closest("section.slide");
      });
    }

    function slideId(sec, i) {
      if (TPL) return sec.getAttribute("data-slide-id") || "";
      var id = sec.getAttribute("data-leo-id");
      if (!id) {
        id = "slide-" + (i + 1);
        sec.setAttribute("data-leo-id", id);
      }
      return id;
    }

    function chrome(tag, cls) {
      var el = document.createElement(tag);
      el.className = "leo-edit-chrome leo-review-ui " + cls;
      return el;
    }

    function clear() {
      closeMenu();
      hideHover();
      document.querySelectorAll(".leo-review-ui").forEach(function (n) {
        n.remove();
      });
      root.classList.remove("leo-review");
    }

    function setStatus(sid, st) {
      var box = document.querySelector('.leo-review-box[data-for="' + String(sid).replace(/"/g, "") + '"]');
      if (!box) return;
      var el = box.querySelector(".leo-review-status");
      el.textContent = (st && st.text) || "";
      el.setAttribute("data-state", (st && st.state) || "");
      if (typeof box._syncSend === "function") box._syncSend();
    }

    function autosize(ta) {
      ta.style.height = "auto";
      ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
    }

    function build() {
      clear();
      readUi();
      root.classList.add("leo-review");
      slides().forEach(function (sec, i) {
        var sid = slideId(sec, i);
        if (!sid) return;
        var box = chrome("div", "leo-review-box");
        box.setAttribute("data-for", sid);
        box.innerHTML =
          '<div class="leo-review-head"><span>Слайд ' + (i + 1) + '</span><span class="leo-review-status"></span></div>' +
          '<div class="leo-review-row"><textarea rows="2" placeholder="Что поправить в этом слайде? Например: сократи заголовок, добавь цифры из заметки"></textarea>' +
          '<button type="button" class="leo-review-send" title="Применить правку к этому слайду" aria-label="Применить правку к слайду ' + (i + 1) + '">' +
          '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>' +
          "</button></div>";
        var ta = box.querySelector("textarea");
        var send = box.querySelector(".leo-review-send");
        ta.setAttribute("aria-label", "Комментарий к слайду " + (i + 1));
        ta.value = comments[sid] || "";
        function syncSend() {
          var st = statuses[sid];
          send.disabled = !ta.value.trim() || !!(st && st.state === "busy");
        }
        function sendThis() {
          if (send.disabled) return;
          comments[sid] = ta.value;
          post("reviseSlide", { slideId: sid, text: ta.value });
        }
        ta.addEventListener("input", function () {
          comments[sid] = ta.value;
          autosize(ta);
          syncSend();
          post("comment", { slideId: sid, text: ta.value });
        });
        ta.addEventListener("keydown", function (e) {
          e.stopPropagation();
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            sendThis();
          }
        });
        send.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          sendThis();
        });
        box._syncSend = syncSend;
        syncSend();
        if (TPL) buildAddSlide(box, sid, i);
        sec.parentNode.insertBefore(box, sec.nextSibling);
        setTimeout(function () {
          autosize(ta);
        }, 0);
        setStatus(sid, statuses[sid]);
        if (TPL) buildTools(sec, sid, i);
      });
      if (TPL) buildEmptyGroups();
    }

    // «+ Слайд после этого»: ИИ делает новый слайд по описанию (или продолжает тему).
    function buildAddSlide(box, sid, i) {
      var wrap = document.createElement("div");
      wrap.className = "leo-add-slide";
      wrap.innerHTML =
        '<button type="button" class="leo-add-slide-toggle">+ Слайд после этого</button>' +
        '<div class="leo-add-slide-form" hidden>' +
        '<div class="leo-layout-chips" role="radiogroup" aria-label="Макет нового слайда"></div>' +
        '<div class="leo-review-row"><textarea rows="2"></textarea>' +
        '<button type="button" class="leo-add-slide-go">Добавить</button></div></div>';
      var toggle = wrap.querySelector(".leo-add-slide-toggle");
      var form = wrap.querySelector(".leo-add-slide-form");
      var ta = form.querySelector("textarea");
      var go = form.querySelector(".leo-add-slide-go");
      var chipsBox = form.querySelector(".leo-layout-chips");
      var layout = "auto";
      function chip(value, label, removable) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "leo-layout-chip" + (value === layout ? " is-on" : "");
        b.setAttribute("role", "radio");
        b.setAttribute("aria-checked", String(value === layout));
        b.textContent = label;
        b.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          if (e.target.closest(".leo-layout-del")) return;
          layout = value;
          paintChips();
        });
        if (removable) {
          var x = document.createElement("span");
          x.className = "leo-layout-del";
          x.setAttribute("role", "button");
          x.setAttribute("aria-label", "Удалить макет «" + label + "»");
          x.textContent = "×";
          x.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            post("deleteLayout", { id: removable });
          });
          b.appendChild(x);
        }
        chipsBox.appendChild(b);
      }
      function paintChips() {
        chipsBox.innerHTML = "";
        chip("auto", "Подобрать макет");
        chip("custom", "Свой макет");
        layouts.forEach(function (l) {
          chip("saved:" + l.id, "★ " + l.name, l.id);
        });
        if (layout.indexOf("saved:") === 0 && !layouts.some(function (l) { return "saved:" + l.id === layout; })) layout = "auto";
        ta.placeholder = LAYOUT_HINTS[layout.indexOf("saved:") === 0 ? "saved" : layout];
      }
      paintChips();
      ta.setAttribute("aria-label", "Описание нового слайда после слайда " + (i + 1));
      toggle.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        form.hidden = !form.hidden;
        toggle.classList.toggle("is-open", !form.hidden);
        if (!form.hidden) ta.focus();
      });
      function submit() {
        var st = statuses[sid];
        if (st && st.state === "busy") return;
        post("insertSlide", { slideId: sid, text: ta.value, layout: layout });
      }
      go.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        submit();
      });
      ta.addEventListener("keydown", function (e) {
        e.stopPropagation();
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          submit();
        }
      });
      ta.addEventListener("input", function () {
        autosize(ta);
      });
      box.appendChild(wrap);
    }

    function buildTools(sec, sid, i) {
      var list = variantsUi[sid] || [];
      var cur = sec.getAttribute("data-variant") || "";
      var curName = "Макет";
      list.forEach(function (v) {
        if (v.id === cur) curName = v.name;
      });
      var tools = chrome("div", "leo-slide-tools");
      tools.innerHTML =
        '<button type="button" data-act="variant" class="leo-variant-btn" aria-haspopup="menu" title="Другой вариант дизайна"><span></span><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>' +
        '<button type="button" data-act="up" title="Переместить выше" aria-label="Переместить слайд выше">↑</button>' +
        '<button type="button" data-act="down" title="Переместить ниже" aria-label="Переместить слайд ниже">↓</button>' +
        '<button type="button" data-act="delslide" title="Удалить слайд" aria-label="Удалить слайд">✕</button>';
      if (cur === "custom.grid") {
        tools.insertAdjacentHTML(
          "afterbegin",
          '<button type="button" data-act="savelayout" title="Сохранить в «Мои макеты»" aria-label="Сохранить макет">☆ Сохранить макет</button>'
        );
      }
      tools.querySelector(".leo-variant-btn span").textContent = curName;
      if (list.length < 2) tools.querySelector(".leo-variant-btn").disabled = true;
      var all = slides();
      if (i === 0) tools.querySelector('[data-act="up"]').disabled = true;
      if (i === all.length - 1) tools.querySelector('[data-act="down"]').disabled = true;
      if (all.length < 2) tools.querySelector('[data-act="delslide"]').disabled = true;
      var delArmed = null;
      tools.addEventListener("click", function (e) {
        var b = e.target.closest("button[data-act]");
        if (!b || b.disabled) return;
        e.preventDefault();
        e.stopPropagation();
        var act = b.getAttribute("data-act");
        if (act === "variant") {
          if (menu && menu.parentNode === tools) closeMenu();
          else openMenu(tools, sid, cur, list);
        } else if (act === "savelayout") {
          post("saveLayout", { slideId: sid });
        } else if (act === "up" || act === "down") {
          post("op", { op: "move_slide", slideId: sid, delta: act === "up" ? -1 : 1 });
        } else if (act === "delslide") {
          if (delArmed) {
            clearTimeout(delArmed);
            post("op", { op: "delete_slide", slideId: sid });
            return;
          }
          b.textContent = "Удалить?";
          b.classList.add("is-armed");
          delArmed = setTimeout(function () {
            delArmed = null;
            b.textContent = "✕";
            b.classList.remove("is-armed");
          }, 3000);
        }
      });
      sec.appendChild(tools);
    }

    function openMenu(tools, sid, cur, list) {
      closeMenu();
      var m = chrome("div", "leo-variant-menu");
      m.setAttribute("role", "menu");
      list.forEach(function (v) {
        var b = document.createElement("button");
        b.type = "button";
        b.setAttribute("role", "menuitemradio");
        b.setAttribute("aria-checked", String(v.id === cur));
        b.innerHTML = '<span class="leo-vm-name"></span><span class="leo-vm-note"></span>';
        b.querySelector(".leo-vm-name").textContent = v.name;
        b.querySelector(".leo-vm-note").textContent = v.id === cur ? "✓" : v.ok ? "" : v.why;
        if (!v.ok && v.id !== cur) b.disabled = true;
        b.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          closeMenu();
          if (v.id !== cur && v.ok) post("op", { op: "variant", slideId: sid, variant: v.id });
        });
        m.appendChild(b);
      });
      tools.appendChild(m);
      menu = m;
      var first = m.querySelector("button:not([disabled])");
      if (first) first.focus();
    }

    function closeMenu() {
      if (menu) menu.remove();
      menu = null;
    }

    function groupLimits(sec, group) {
      var g = sec.querySelector('[data-group="' + group.replace(/"/g, "") + '"][data-max]');
      if (!g) return null;
      var re = new RegExp("^" + group.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\.\\d+$");
      var count = [].slice.call(sec.querySelectorAll("[data-item]")).filter(function (n) {
        return re.test(n.getAttribute("data-item") || "");
      }).length;
      return { min: +g.getAttribute("data-min") || 0, max: +g.getAttribute("data-max") || 99, count: count };
    }

    function buildEmptyGroups() {
      slides().forEach(function (sec) {
        sec.querySelectorAll("[data-group][data-max]").forEach(function (g) {
          var group = g.getAttribute("data-group");
          var lim = groupLimits(sec, group);
          if (!lim || lim.count > 0 || lim.max < 1 || g.style.display === "none") return;
          var b = chrome("button", "leo-add-empty");
          b.type = "button";
          b.textContent = "+ Добавить";
          b.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            post("op", { op: "add_item", slideId: sec.getAttribute("data-slide-id"), group: group });
          });
          g.appendChild(b);
        });
      });
    }

    function hideHover() {
      if (hover) hover.remove();
      hover = null;
      hoverItem = null;
    }

    function showHover(item) {
      var sec = item.closest("section.slide");
      if (!sec) return;
      var path = item.getAttribute("data-item") || "";
      var dot = path.lastIndexOf(".");
      var group = path.slice(0, dot);
      var index = +path.slice(dot + 1);
      var lim = groupLimits(sec, group);
      if (!lim) return;
      hideHover();
      hoverItem = item;
      hover = chrome("div", "leo-item-tools");
      hover.innerHTML =
        '<button type="button" data-act="add" title="Добавить такой же элемент после">+</button>' +
        '<button type="button" data-act="remove" title="Удалить элемент">×</button>';
      var add = hover.querySelector('[data-act="add"]');
      var rem = hover.querySelector('[data-act="remove"]');
      // На пределе кнопка не «мёртвая»: по нажатию объясняем ограничение.
      if (lim.count >= lim.max) {
        add.classList.add("is-limit");
        add.title = "В этом блоке максимум " + lim.max;
      }
      if (lim.count <= lim.min) {
        rem.classList.add("is-limit");
        rem.title = lim.min ? "В этом блоке минимум " + lim.min : "Нечего удалять";
      }
      hover.addEventListener("click", function (e) {
        var b = e.target.closest("button[data-act]");
        if (!b) return;
        e.preventDefault();
        e.stopPropagation();
        if (b.classList.contains("is-limit")) {
          post("notice", { text: b.title });
          return;
        }
        var sid = sec.getAttribute("data-slide-id");
        if (b.getAttribute("data-act") === "add") post("op", { op: "add_item", slideId: sid, group: group, after: index });
        else post("op", { op: "remove_item", slideId: sid, group: group, index: index });
        hideHover();
      });
      var r = item.getBoundingClientRect();
      var sr = sec.getBoundingClientRect();
      hover.style.top = Math.max(4, r.top - sr.top + 4) + "px";
      hover.style.left = Math.max(4, r.right - sr.left - 64) + "px";
      sec.appendChild(hover);
    }

    document.addEventListener("mousemove", function (e) {
      if (!on || !TPL) return;
      var t = e.target;
      if (hover && (t === hover || hover.contains(t))) return;
      var item = t && t.closest ? t.closest("[data-item]") : null;
      if (item && item.closest(".leo-edit-chrome")) item = null;
      if (item === hoverItem) return;
      if (item) showHover(item);
      else hideHover();
    });

    document.addEventListener(
      "click",
      function (e) {
        if (menu && !menu.contains(e.target) && !e.target.closest(".leo-variant-btn")) closeMenu();
      },
      true
    );
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeMenu();
    });

    window.addEventListener("message", function (ev) {
      var d = ev && ev.data;
      if (!d || d.source !== "leo-web-canvas-host") return;
      if (d.type === "review") {
        on = !!d.on;
        comments = Object.assign({}, d.comments || {});
        statuses = Object.assign({}, d.statuses || {});
        layouts = Array.isArray(d.layouts) ? d.layouts : [];
        if (on) {
          build();
          if (d.scrollY) {
            setTimeout(function () {
              window.scrollTo(0, d.scrollY);
            }, 0);
          }
        } else clear();
      }
      if (d.type === "getScroll") post("scroll", { scrollY: window.scrollY || 0 });
      if (d.type === "reviewStatus") {
        Object.keys(d.statuses || {}).forEach(function (sid) {
          statuses[sid] = d.statuses[sid];
          setStatus(sid, d.statuses[sid]);
        });
        (d.clear || []).forEach(function (sid) {
          delete comments[sid];
          var ta = document.querySelector('.leo-review-box[data-for="' + String(sid).replace(/"/g, "") + '"] textarea');
          if (ta) {
            ta.value = "";
            autosize(ta);
          }
        });
      }
    });
  }

  var REVIEW_STYLE =
    "<style id=\"leo-canvas-review-style\">" +
    "html.leo-review body{overflow:auto!important;height:auto!important}" +
    // Review UI reuses .leo-edit-chrome (so snapshots strip it) but not its floating-toolbar look.
    ".leo-edit-chrome.leo-review-ui{position:static;top:auto;right:auto;display:block;flex-wrap:nowrap;gap:0;padding:0;border-radius:0;background:transparent;box-shadow:none}" +
    ".leo-edit-chrome.leo-slide-tools{position:absolute;display:flex;gap:4px;top:10px;right:10px}" +
    ".leo-edit-chrome.leo-item-tools{position:absolute;display:flex;gap:4px}" +
    ".leo-edit-chrome.leo-variant-menu{position:absolute;display:grid}" +
    "html.leo-review section.slide{position:relative}" +
    ".leo-edit-chrome.leo-review-box{margin:6px 0 18px;padding:10px 12px;border-radius:10px;background:rgba(255,255,255,.96);box-shadow:0 2px 10px rgba(0,0,0,.18);font:13px/1.4 ui-sans-serif,system-ui,-apple-system,sans-serif;color:#18201d}" +
    ".leo-review-head{display:flex;justify-content:space-between;gap:10px;margin-bottom:6px;font-size:12px;font-weight:600;color:#4b5a54}" +
    ".leo-review-status[data-state=busy]{color:#4b5a54}.leo-review-status[data-state=ok]{color:#16805f}.leo-review-status[data-state=warn]{color:#9a6a12}.leo-review-status[data-state=err]{color:#b2465c}" +
    ".leo-review-box textarea{display:block;width:100%;box-sizing:border-box;min-height:40px;resize:none;border:1px solid #cfd8d4;border-radius:8px;padding:7px 9px;font:inherit;color:inherit;background:#fff}" +
    ".leo-review-box textarea:focus{outline:2px solid #3dd6a5;outline-offset:1px;border-color:transparent}" +
    ".leo-review-row{display:flex;align-items:flex-end;gap:8px}" +
    ".leo-review-row textarea{flex:1 1 auto;min-width:0}" +
    ".leo-review-send{appearance:none;flex:0 0 auto;display:inline-grid;place-items:center;width:34px;height:34px;border:0;border-radius:50%;background:#18201d;color:#fff;cursor:pointer}" +
    ".leo-review-send:disabled{opacity:.3;cursor:default}" +
    ".leo-add-slide{margin-top:8px}" +
    ".leo-layout-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 8px}" +
    ".leo-layout-chip{appearance:none;display:inline-flex;align-items:center;gap:6px;border:1px solid #cfd8d4;border-radius:999px;background:#fff;color:#18201d;padding:5px 12px;font:inherit;font-size:13px;cursor:pointer}" +
    ".leo-layout-chip.is-on{background:#18201d;border-color:#18201d;color:#fff}" +
    ".leo-layout-del{display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;font-size:13px;line-height:1;opacity:.6}" +
    ".leo-layout-del:hover{opacity:1;background:rgba(0,0,0,.12)}" +
    ".leo-add-slide-toggle{appearance:none;border:0;background:none;padding:4px 0;font:inherit;font-size:13px;font-weight:600;color:#16805f;cursor:pointer}" +
    ".leo-add-slide-toggle.is-open{color:#4b5a54}" +
    ".leo-add-slide-form{margin-top:4px}" +
    ".leo-add-slide-form[hidden]{display:none}" +
    ".leo-add-slide-go{appearance:none;flex:0 0 auto;height:34px;padding:0 14px;border:0;border-radius:17px;background:#18201d;color:#fff;font:inherit;font-size:13px;font-weight:600;cursor:pointer}" +
    ".leo-review-send:focus-visible{outline:2px solid #3dd6a5;outline-offset:2px}" +
    ".leo-item-tools>button.is-limit{opacity:.45}" +
    ".leo-slide-tools{position:absolute;top:10px;right:10px;z-index:60;display:flex;gap:4px;font:12px/1 ui-sans-serif,system-ui,-apple-system,sans-serif}" +
    ".leo-slide-tools>button,.leo-item-tools>button,.leo-add-empty{appearance:none;display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 10px;border-radius:8px;border:1px solid rgba(10,10,10,.12);background:rgba(255,255,255,.95);color:#18201d;font:inherit;font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.2)}" +
    ".leo-slide-tools>button:disabled,.leo-item-tools>button:disabled{opacity:.45;cursor:default}" +
    ".leo-slide-tools>button.is-armed{background:#b2465c;color:#fff;border-color:#b2465c}" +
    ".leo-slide-tools button:focus-visible,.leo-variant-menu button:focus-visible{outline:2px solid #3dd6a5;outline-offset:2px}" +
    ".leo-edit-chrome.leo-variant-menu{top:34px;right:0;min-width:260px;max-height:320px;overflow:auto;padding:4px;border-radius:10px;background:#fff;box-shadow:0 8px 28px rgba(0,0,0,.28);gap:2px}" +
    ".leo-variant-menu button{appearance:none;display:flex;justify-content:space-between;align-items:center;gap:12px;width:100%;padding:8px 10px;border:0;border-radius:7px;background:transparent;color:#18201d;font:inherit;font-size:13px;text-align:left;cursor:pointer}" +
    ".leo-variant-menu button:hover:not(:disabled){background:#eef4f1}" +
    ".leo-variant-menu button[aria-checked=true]{font-weight:700;color:#16805f}" +
    ".leo-variant-menu button:disabled{color:#8a9691;cursor:default}" +
    ".leo-vm-note{font-size:11px;color:#8a9691;font-weight:400;text-align:right}" +
    ".leo-item-tools{position:absolute;z-index:55;display:flex;gap:4px;font:14px/1 ui-sans-serif,system-ui,sans-serif}" +
    ".leo-item-tools>button{height:26px;width:28px;justify-content:center;padding:0}" +
    ".leo-edit-chrome.leo-add-empty{display:inline-flex;height:auto;padding:10px 16px;font-size:16px;border-style:dashed;background:rgba(255,255,255,.12);color:inherit;box-shadow:none}" +
    "html.leo-review [data-slot][contenteditable=true]:hover{outline:1px dashed rgba(61,214,165,.6);outline-offset:3px}" +
    "html.leo-review [data-slot][contenteditable=true]:focus{outline:2px solid rgba(61,214,165,.9);outline-offset:3px}" +
    "</style>";

  var EDIT_STYLE =
    "<style id=\"leo-canvas-edit-style\">" +
    "html.leo-edit-mode [data-leo-id]{cursor:default}" +
    "html.leo-edit-mode [contenteditable=true]{cursor:text;outline:1px dashed rgba(10,10,10,.22);outline-offset:2px}" +
    "[data-leo-id].leo-edit-selected{outline:2px solid #3b82f6;outline-offset:3px}" +
    "[data-leo-id].leo-dragging{opacity:.55}" +
    "[data-leo-id].leo-drop-target{outline:2px dashed #3b82f6;outline-offset:4px}" +
    ".leo-edit-chrome{position:absolute;z-index:40;top:8px;right:8px;display:flex;flex-wrap:wrap;gap:4px;padding:4px;border-radius:8px;background:rgba(255,255,255,.94);box-shadow:0 4px 16px rgba(0,0,0,.14);font-family:ui-sans-serif,system-ui,sans-serif}" +
    ".leo-edit-chrome button{appearance:none;border:1px solid rgba(10,10,10,.14);border-radius:6px;background:#fff;color:#111;font:inherit;font-size:11px;padding:4px 8px;cursor:pointer;white-space:nowrap}" +
    ".leo-edit-chrome .leo-drag-handle{cursor:grab;font-size:14px;line-height:1;padding:4px 6px;user-select:none}" +
    ".media-slot{position:relative;width:100%;min-height:12rem;overflow:hidden;border-radius:12px}" +
    ".media-img{display:block;width:100%;height:100%;min-height:12rem;object-fit:cover;border-radius:12px}" +
    "</style>";

  function injectPicker(html) {
    var raw = String(html || "");
    var tag =
      EDIT_STYLE +
      REVIEW_STYLE +
      '<script id="leo-canvas-picker">\n' +
      FRAME_SCRIPT +
      "\n(" +
      frameReviewMain.toString() +
      ")();\n</script>";
    if (/<\/body>/i.test(raw)) {
      return raw.replace(/<\/body>/i, tag + "</body>");
    }
    return raw + tag;
  }

  function svgIcon(paths) {
    return (
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      paths +
      "</svg>"
    );
  }

  // Стили презентаций из шаблонов (assistant/lib/canvas_templates.THEMES).
  var DECK_THEMES = [
    { id: "analytic", name: "Аналитика", hint: "тёмный, зелёные акценты", colors: ["#0c1412", "#3dd6a5", "#5b8def"] },
    { id: "mono", name: "Монохром", hint: "чёрный, белый и голубой", colors: ["#0d0d0e", "#ffffff", "#dceaf6"] },
  ];

  var INSERT_ITEMS = [
    {
      kind: "heading",
      label: "Заголовок",
      icon: svgIcon('<path d="M6 4v16"/><path d="M18 4v16"/><path d="M6 12h12"/>'),
    },
    {
      kind: "text",
      label: "Текст",
      icon: svgIcon(
        '<path d="M4 6h16"/><path d="M4 12h16"/><path d="M4 18h10"/>'
      ),
    },
    {
      kind: "pill",
      label: "Плашка",
      icon: svgIcon('<rect x="3" y="8" width="18" height="8" rx="4"/>'),
    },
    {
      kind: "metric",
      label: "Метрика",
      icon: svgIcon(
        '<path d="M4 19V9"/><path d="M10 19V5"/><path d="M16 19v-7"/><path d="M22 19V8"/>'
      ),
    },
    {
      kind: "image",
      label: "Картинка",
      icon: svgIcon(
        '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="m21 15-4.5-4.5L7 20"/>'
      ),
    },
    {
      kind: "slide",
      label: "Слайд",
      icon: svgIcon(
        '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 22h8"/><path d="M12 18v4"/>'
      ),
    },
  ];

  var DELETE_ICON = svgIcon(
    '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/>'
  );

  function create(opts) {
    opts = opts || {};
    var host = opts.host;
    if (!host) throw new Error("LeoWebCanvas: host required");
    var onSelect = typeof opts.onSelect === "function" ? opts.onSelect : null;
    var onRefresh = typeof opts.onRefresh === "function" ? opts.onRefresh : null;
    var onChange = typeof opts.onChange === "function" ? opts.onChange : null;
    var onMedia = typeof opts.onMedia === "function" ? opts.onMedia : null;
    // onOp({op, slideId, …, html}) → Promise<new html>; onRevise({comments, html}) → Promise<{html, results}>
    var onOp = typeof opts.onOp === "function" ? opts.onOp : null;
    var onRevise = typeof opts.onRevise === "function" ? opts.onRevise : null;
    var onNotify = typeof opts.onNotify === "function" ? opts.onNotify : function () {};
    var sheetId = String(opts.sheetId || "");
    var wrap = document.createElement("div");
    wrap.className = "note-web-canvas";
    var toolbar = document.createElement("div");
    toolbar.className = "note-web-canvas-toolbar";

    function makeBtn(label, extraClass) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "note-web-canvas-pick-btn" + (extraClass ? " " + extraClass : "");
      btn.textContent = label;
      return btn;
    }

    function makeIconBtn(label, iconHtml, extraClass) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className =
        "note-web-canvas-pick-btn note-web-canvas-icon-btn" +
        (extraClass ? " " + extraClass : "");
      btn.setAttribute("aria-label", label);
      btn.title = label;
      btn.innerHTML = iconHtml;
      return btn;
    }

    var pickBtn = makeBtn("Выделить");
    var editBtn = makeBtn("Редактировать", "note-web-canvas-edit-btn");
    var refreshBtn = makeBtn("Обновить", "note-web-canvas-refresh-btn");
    refreshBtn.title = "Перезагрузить полотно";
    var applyBtn = makeBtn("Применить правки", "note-web-canvas-apply-btn");
    applyBtn.hidden = true;
    applyBtn.title = "Отправить комментарии к слайдам ИИ одним запросом";
    // Стиль оформления презентации из шаблонов: перерисовка без ИИ, содержимое не меняется.
    var themeWrap = document.createElement("div");
    themeWrap.className = "note-web-canvas-theme";
    themeWrap.hidden = true;
    var themeBtn = makeBtn("Стиль", "note-web-canvas-theme-btn");
    themeBtn.setAttribute("aria-haspopup", "menu");
    themeBtn.setAttribute("aria-expanded", "false");
    var themeMenu = document.createElement("div");
    themeMenu.className = "note-web-canvas-theme-menu";
    themeMenu.setAttribute("role", "menu");
    themeMenu.hidden = true;
    themeWrap.appendChild(themeBtn);
    themeWrap.appendChild(themeMenu);
    toolbar.appendChild(pickBtn);
    toolbar.appendChild(editBtn);
    toolbar.appendChild(themeWrap);
    toolbar.appendChild(applyBtn);
    toolbar.appendChild(refreshBtn);

    var insertBar = document.createElement("div");
    insertBar.className = "note-web-canvas-insert-bar";
    insertBar.hidden = true;
    INSERT_ITEMS.forEach(function (item) {
      var b = makeIconBtn(
        "Добавить: " + item.label,
        item.icon,
        "note-web-canvas-insert-btn"
      );
      b.addEventListener("click", function (e) {
        e.preventDefault();
        if (!editing) setEdit(true);
        postToFrame({ type: "insert", kind: item.kind });
      });
      insertBar.appendChild(b);
    });
    var delBtn = makeIconBtn(
      "Удалить выбранный блок",
      DELETE_ICON,
      "note-web-canvas-insert-btn note-web-canvas-del-btn"
    );
    delBtn.addEventListener("click", function (e) {
      e.preventDefault();
      postToFrame({ type: "deleteSelected" });
    });
    insertBar.appendChild(delBtn);
    toolbar.appendChild(insertBar);

    var frame = document.createElement("iframe");
    frame.className = "note-web-canvas-frame";
    frame.setAttribute("sandbox", "allow-scripts");
    frame.setAttribute("title", "Web canvas");
    wrap.appendChild(toolbar);
    wrap.appendChild(frame);
    host.innerHTML = "";
    host.appendChild(wrap);

    var picking = false;
    var editing = false;
    var htmlDoc = "";
    var ready = false;
    var dirtyTimer = null;
    var busy = false;
    var statuses = {};
    var snapshotWaiters = [];
    var comments = loadComments();

    function commentsKey() {
      return "leo-canvas-comments:" + sheetId;
    }

    function loadComments() {
      try {
        var raw = global.localStorage && global.localStorage.getItem(commentsKey());
        var data = raw ? JSON.parse(raw) : {};
        return data && typeof data === "object" ? data : {};
      } catch (e) {
        return {};
      }
    }

    function saveComments() {
      try {
        if (global.localStorage) global.localStorage.setItem(commentsKey(), JSON.stringify(comments));
      } catch (e) {}
    }

    function pendingComments() {
      var out = {};
      Object.keys(comments).forEach(function (sid) {
        var t = String(comments[sid] || "").trim();
        if (t) out[sid] = t;
      });
      return out;
    }

    function isTemplateDeck() {
      return /<html\b[^>]*\bdata-leo-deck\b/i.test(htmlDoc);
    }

    function syncApplyBtn() {
      var n = Object.keys(pendingComments()).length;
      applyBtn.hidden = !editing;
      applyBtn.disabled = busy || n === 0;
      applyBtn.textContent = busy ? "Применяю…" : n ? "Применить правки (" + n + ")" : "Применить правки";
      applyBtn.classList.toggle("is-active", n > 0 && !busy);
    }

    var restoreScrollY = 0;

    function postReview() {
      postToFrame({
        type: "review",
        on: editing,
        comments: comments,
        statuses: statuses,
        scrollY: restoreScrollY,
        layouts: typeof opts.getLayouts === "function" ? opts.getLayouts() || [] : [],
      });
      restoreScrollY = 0;
    }

    // Ask the frame for its current HTML (inline text edits) before a server op.
    function requestSnapshot() {
      return new Promise(function (resolve) {
        if (!ready) {
          resolve(htmlDoc);
          return;
        }
        var done = false;
        var finish = function (html) {
          if (done) return;
          done = true;
          resolve(html || htmlDoc);
        };
        snapshotWaiters.push(finish);
        postToFrame({ type: "getHtml" });
        setTimeout(function () {
          finish(htmlDoc);
        }, 1500);
      });
    }

    function setBusy(on) {
      busy = !!on;
      wrap.classList.toggle("is-busy", busy);
      syncApplyBtn();
      syncThemeBtn();
    }

    function runOp(req) {
      if (!onOp || busy) return;
      setBusy(true);
      requestSnapshot()
        .then(function (html) {
          return onOp(Object.assign({ sheetId: sheetId, html: html }, req));
        })
        .then(function (html) {
          if (html) setHtml(html);
        })
        .catch(function (err) {
          onNotify((err && err.message) || "Не удалось изменить слайд");
        })
        .then(function () {
          setBusy(false);
        });
    }

    // onlyIds — правка одного слайда кнопкой под ним; без аргумента — все комментарии.
    function runRevise(onlyIds) {
      var pending = pendingComments();
      if (onlyIds && onlyIds.length) {
        var only = {};
        onlyIds.forEach(function (sid) {
          if (pending[sid]) only[sid] = pending[sid];
        });
        pending = only;
      }
      var ids = Object.keys(pending);
      if (!onRevise || busy || !ids.length) return;
      setBusy(true);
      postToFrame({ type: "getScroll" });
      ids.forEach(function (sid) {
        statuses[sid] = { state: "busy", text: "Применяется…" };
      });
      postToFrame({ type: "reviewStatus", statuses: statuses });
      requestSnapshot()
        .then(function (html) {
          return onRevise({ sheetId: sheetId, comments: pending, html: html });
        })
        .then(function (res) {
          var results = (res && res.results) || {};
          var applied = [];
          ids.forEach(function (sid) {
            var r = results[sid] || "failed";
            if (r === "applied") {
              applied.push(sid);
              delete comments[sid];
              statuses[sid] = { state: "ok", text: "Применено" };
            } else if (r === "unchanged") {
              statuses[sid] = {
                state: "warn",
                text: "ИИ не нашёл, что поменять. Уточните: какой текст, пункт или макет изменить",
              };
            } else {
              statuses[sid] = { state: "err", text: "Не удалось применить — попробуйте ещё раз" };
            }
          });
          saveComments();
          if (res && res.html) setHtml(res.html);
          else postToFrame({ type: "reviewStatus", statuses: statuses, clear: applied });
          onNotify(
            ids.length === 1
              ? applied.length
                ? "Слайд обновлён"
                : "Правка не применилась — см. подсказку под слайдом"
              : applied.length
                ? "Применено к слайдам: " + applied.length + " из " + ids.length
                : "Правки не применились — см. подсказки под слайдами"
          );
        })
        .catch(function (err) {
          ids.forEach(function (sid) {
            statuses[sid] = { state: "err", text: "Не удалось применить — попробуйте ещё раз" };
          });
          postToFrame({ type: "reviewStatus", statuses: statuses });
          onNotify((err && err.message) || "Не удалось применить правки");
        })
        .then(function () {
          setBusy(false);
        });
    }

    function runInsert(sid, text, layout) {
      if (!onRevise || busy) return;
      setBusy(true);
      postToFrame({ type: "getScroll" });
      statuses[sid] = { state: "busy", text: "Добавляю слайд…" };
      postToFrame({ type: "reviewStatus", statuses: statuses });
      requestSnapshot()
        .then(function (html) {
          return onRevise({ sheetId: sheetId, comments: {}, html: html, insertAfter: sid, insertText: text, insertLayout: layout || "auto" });
        })
        .then(function (res) {
          var n = ((res && res.newIds) || []).length;
          if (res && res.html && n) {
            statuses[sid] = { state: "ok", text: n > 1 ? "Ниже добавлено слайдов: " + n : "Ниже добавлен новый слайд" };
            setHtml(res.html);
            onNotify(n > 1 ? "Добавлено слайдов: " + n : "Слайд добавлен");
          } else {
            statuses[sid] = { state: "err", text: "Не получилось добавить слайд — уточните, что на нём должно быть" };
            postToFrame({ type: "reviewStatus", statuses: statuses });
            onNotify("Слайд не добавлен");
          }
        })
        .catch(function (err) {
          statuses[sid] = { state: "err", text: (err && err.message) || "Не получилось добавить слайд" };
          postToFrame({ type: "reviewStatus", statuses: statuses });
          onNotify((err && err.message) || "Не получилось добавить слайд");
        })
        .then(function () {
          setBusy(false);
        });
    }

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
      var keepEdit = editing;
      var keepPick = picking;
      frame.srcdoc = injectPicker(htmlDoc);
      picking = keepPick;
      editing = keepEdit;
    }

    function getHtml() {
      return htmlDoc;
    }

    function currentDeckTheme() {
      var m = /<html\b[^>]*\bdata-leo-theme="([a-z-]+)"/i.exec(htmlDoc);
      return (m && m[1]) || "analytic";
    }

    function closeThemeMenu() {
      themeMenu.hidden = true;
      themeBtn.setAttribute("aria-expanded", "false");
    }

    function renderThemeMenu() {
      var cur = currentDeckTheme();
      themeMenu.innerHTML = "";
      DECK_THEMES.forEach(function (t) {
        var b = document.createElement("button");
        b.type = "button";
        b.className = "note-web-canvas-theme-item" + (t.id === cur ? " is-active" : "");
        b.setAttribute("role", "menuitemradio");
        b.setAttribute("aria-checked", String(t.id === cur));
        var sw = document.createElement("span");
        sw.className = "note-web-canvas-theme-swatch";
        t.colors.forEach(function (c) {
          var i = document.createElement("i");
          i.style.background = c;
          sw.appendChild(i);
        });
        var txt = document.createElement("span");
        txt.className = "note-web-canvas-theme-text";
        var name = document.createElement("b");
        name.textContent = t.name;
        var hint = document.createElement("small");
        hint.textContent = t.hint;
        txt.appendChild(name);
        txt.appendChild(hint);
        b.appendChild(sw);
        b.appendChild(txt);
        b.addEventListener("click", function (e) {
          e.preventDefault();
          e.stopPropagation();
          closeThemeMenu();
          if (opts.onTheme) opts.onTheme(t.id);
          if (t.id === currentDeckTheme()) return;
          runOp({ op: "theme", theme: t.id });
        });
        themeMenu.appendChild(b);
      });
    }

    themeBtn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (!themeMenu.hidden) return closeThemeMenu();
      if (busy) return;
      renderThemeMenu();
      themeMenu.hidden = false;
      themeBtn.setAttribute("aria-expanded", "true");
    });
    document.addEventListener("click", function (e) {
      if (!themeMenu.hidden && !themeWrap.contains(e.target)) closeThemeMenu();
    });

    function syncThemeBtn() {
      var tpl = isTemplateDeck();
      themeWrap.hidden = !tpl;
      if (!tpl) return closeThemeMenu();
      var cur = currentDeckTheme();
      var name = "Стиль";
      DECK_THEMES.forEach(function (t) {
        if (t.id === cur) name = t.name;
      });
      themeBtn.textContent = "Стиль: " + name + " ▾";
      themeBtn.disabled = busy;
    }

    function syncInsertBar() {
      syncThemeBtn();
      // «Выделить» (элемент → контекст для ИИ в чате) нужен свободным листам; у презентаций из шаблонов
      // для этого есть «Что поправить» у каждого слайда в «Редактировать».
      var tplDeck = isTemplateDeck();
      pickBtn.hidden = tplDeck;
      if (tplDeck && picking) setPick(false);
      // Template decks are edited through slots, comments and the layout menu.
      insertBar.hidden = !editing || isTemplateDeck();
      wrap.classList.toggle("is-editing", editing);
      syncApplyBtn();
    }

    function setPick(on) {
      picking = !!on;
      if (picking && editing) setEdit(false);
      pickBtn.classList.toggle("is-active", picking);
      pickBtn.textContent = picking ? "Отмена" : "Выделить";
      postToFrame({ type: "pick", on: picking });
    }

    function setEdit(on) {
      editing = !!on;
      if (editing && picking) setPick(false);
      editBtn.classList.toggle("is-active", editing);
      editBtn.textContent = editing ? "Готово" : "Редактировать";
      syncInsertBar();
      postToFrame({ type: "edit", on: editing });
      postReview();
      if (!editing && onChange) onChange(htmlDoc);
    }

    function scheduleDirty() {
      if (dirtyTimer) clearTimeout(dirtyTimer);
      dirtyTimer = setTimeout(function () {
        dirtyTimer = null;
        postToFrame({ type: "getHtml" });
      }, 400);
    }

    pickBtn.addEventListener("click", function (e) {
      e.preventDefault();
      setPick(!picking);
    });

    editBtn.addEventListener("click", function (e) {
      e.preventDefault();
      setEdit(!editing);
    });

    applyBtn.addEventListener("click", function (e) {
      e.preventDefault();
      runRevise();
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
        if (editing) {
          postToFrame({ type: "edit", on: true });
          postReview();
        }
        syncInsertBar();
        return;
      }
      if (d.type === "html") {
        htmlDoc = String(d.html || htmlDoc);
        var waiters = snapshotWaiters;
        snapshotWaiters = [];
        waiters.forEach(function (fn) {
          fn(htmlDoc);
        });
        if (onChange) onChange(htmlDoc);
        return;
      }
      if (d.type === "scroll") {
        restoreScrollY = Number(d.scrollY) || 0;
        return;
      }
      if (d.type === "reviseSlide") {
        var rsid = String(d.slideId || "");
        if (!rsid) return;
        comments[rsid] = String(d.text || "");
        saveComments();
        syncApplyBtn();
        if (busy) {
          onNotify("Дождитесь окончания текущей правки");
          return;
        }
        runRevise([rsid]);
        return;
      }
      if (d.type === "insertSlide") {
        var isid = String(d.slideId || "");
        if (!isid) return;
        if (busy) {
          onNotify("Дождитесь окончания текущей правки");
          return;
        }
        runInsert(isid, String(d.text || ""), String(d.layout || "auto"));
        return;
      }
      if (d.type === "saveLayout" && opts.onSaveLayout) {
        if (busy) return;
        requestSnapshot()
          .then(function (html) {
            return opts.onSaveLayout({ sheetId: sheetId, slideId: String(d.slideId || ""), html: html });
          })
          .then(function (item) {
            if (item) {
              onNotify("Макет «" + item.name + "» сохранён — он появится в «+ Слайд после этого» во всех презентациях");
              postReview();
            }
          })
          .catch(function (err) {
            onNotify((err && err.message) || "Не удалось сохранить макет");
          });
        return;
      }
      if (d.type === "deleteLayout" && opts.onDeleteLayout) {
        Promise.resolve(opts.onDeleteLayout(d.id))
          .then(function () {
            onNotify("Макет удалён");
            postReview();
          })
          .catch(function (err) {
            onNotify((err && err.message) || "Не удалось удалить макет");
          });
        return;
      }
      if (d.type === "notice") {
        onNotify(String(d.text || ""));
        return;
      }
      if (d.type === "comment") {
        var sid = String(d.slideId || "");
        if (!sid) return;
        comments[sid] = String(d.text || "");
        if (statuses[sid] && statuses[sid].state !== "busy") delete statuses[sid];
        saveComments();
        syncApplyBtn();
        return;
      }
      if (d.type === "op") {
        restoreScrollY = Number(d.scrollY) || 0;
        runOp({
          op: String(d.op || ""),
          slideId: String(d.slideId || ""),
          variant: d.variant != null ? String(d.variant) : "",
          group: d.group != null ? String(d.group) : "",
          index: Number(d.index) || 0,
          after: d.after == null ? null : Number(d.after),
          delta: Number(d.delta) || 0,
        });
        return;
      }
      if (d.type === "dirty") {
        scheduleDirty();
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
        return;
      }
      if (d.type === "media" && onMedia) {
        onMedia({
          sheetId: sheetId,
          action: String(d.action || ""),
          leoId: String(d.leoId || ""),
          prompt: String(d.prompt || ""),
          hasImg: !!d.hasImg,
          isVisual: !!d.isVisual,
          slideTitle: String(d.slideTitle || ""),
          applyImg: function (dataUrl, prompt) {
            postToFrame({ type: "setImg", leoId: String(d.leoId || ""), dataUrl: dataUrl, prompt: prompt || "" });
          },
          clearMedia: function () {
            postToFrame({ type: "clearMedia", leoId: String(d.leoId || "") });
          },
        });
      }
    }
    window.addEventListener("message", onMessage);

    setHtml(opts.html || "");

    return {
      setHtml: setHtml,
      // Новая версия от другого участника: сохраняем прокрутку в режиме правок.
      setHtmlKeepScroll: function (html) {
        if (busy) return;
        postToFrame({ type: "getScroll" });
        setTimeout(function () {
          setHtml(html);
        }, 120);
      },
      getHtml: getHtml,
      setSheetId: function (id) {
        sheetId = String(id || "");
        comments = loadComments();
      },
      setPick: setPick,
      setEdit: setEdit,
      isEditing: function () {
        return editing;
      },
      requestHtml: function () {
        postToFrame({ type: "getHtml" });
      },
      highlight: function (leoId) {
        postToFrame({ type: "highlight", leoId: leoId });
      },
      destroy: function () {
        window.removeEventListener("message", onMessage);
        if (dirtyTimer) clearTimeout(dirtyTimer);
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
