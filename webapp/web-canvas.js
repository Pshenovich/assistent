/**
 * Leo web-canvas: sandboxed iframe preview, GPT pick, and manual edit mode.
 * Exposes window.LeoWebCanvas.
 */
(function (global) {
  "use strict";

  var FRAME_SCRIPT =
    "(function(){" +
    "var pick=false,edit=false,selected=null,dragState=null;" +
    "var TEXT_SEL='.title,.display,.body,.lead,.caption,.pill,.metric-xl,.tile-num,h1,h2,h3,p,span,blockquote';" +
    "var MEDIA_SEL='.abstract,.media-slot,img.media-img,.pane-stage';" +
    "function post(type,payload){try{parent.postMessage(Object.assign({source:'leo-web-canvas',type:type},payload||{}), '*');}catch(e){}}" +
    "function uid(prefix){return String(prefix||'el')+'-'+Math.random().toString(36).slice(2,8);}" +
    "function clearSel(){if(selected){selected.classList.remove('leo-selected','leo-edit-selected');selected=null;}document.querySelectorAll('.leo-drop-target').forEach(function(n){n.classList.remove('leo-drop-target');});}" +
    "function stripEditChrome(root){" +
    "root=root||document.documentElement;" +
    "root.classList.remove('leo-pick-mode','leo-edit-mode');" +
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
    "function isMediaNode(n){return !!(n&&(n.matches&&n.matches(MEDIA_SEL)||n.classList&&(n.classList.contains('abstract')||n.classList.contains('media-slot')||n.classList.contains('media-img'))));}" +
    "function ensureLeoId(n){if(!n)return'';var id=n.getAttribute('data-leo-id');if(!id){id=uid('block');n.setAttribute('data-leo-id',id);}return id;}" +
    "function showEditChrome(node){" +
    "document.querySelectorAll('.leo-edit-chrome').forEach(function(n){n.remove();});" +
    "if(!node||!edit)return;" +
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
    "e.preventDefault();e.stopPropagation();" +
    "var el=e.target;if(!el||el===document.documentElement||el===document.body)return;" +
    "var node=el.closest('[data-leo-id]')||el;" +
    "if(pick){" +
    "selectNode(node);" +
    "post('select',{leoId:node.getAttribute('data-leo-id')||'',label:(node.innerText||'').trim().slice(0,80),snippet:(node.outerHTML||'').slice(0,4000),tag:node.tagName});" +
    "return;" +
    "}" +
    "if(edit)selectNode(node);" +
    "}" +
    "function onInput(){if(edit)post('dirty',{});}" +
    "function applyImg(leoId,dataUrl){" +
    "var n=leoId&&document.querySelector('[data-leo-id=\"'+String(leoId).replace(/\"/g,'')+'\"]');" +
    "if(!n||!dataUrl)return;" +
    "var img=document.createElement('img');" +
    "img.className='media-img';" +
    "img.setAttribute('data-leo-id',leoId);" +
    "img.alt='';" +
    "img.src=dataUrl;" +
    "if(n.tagName&&n.tagName.toLowerCase()==='img'){n.src=dataUrl;n.classList.add('media-img');}" +
    "else{n.innerHTML='';n.classList.add('media-slot');n.appendChild(img);}" +
    "selectNode(n.tagName.toLowerCase()==='img'?n:n);" +
    "snapshotHtml();" +
    "}" +
    "function clearMedia(leoId){" +
    "var n=leoId&&document.querySelector('[data-leo-id=\"'+String(leoId).replace(/\"/g,'')+'\"]');" +
    "if(!n)return;" +
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
    "if(d.type==='setImg'){applyImg(d.leoId,d.dataUrl);}" +
    "if(d.type==='clearMedia'){clearMedia(d.leoId);}" +
    "if(d.type==='insert'){insertBlock(String(d.kind||'text'));}" +
    "if(d.type==='deleteSelected'){if(selected)deleteNode(selected);}" +
    "});" +
    "document.addEventListener('click',onClick,true);" +
    "document.addEventListener('input',onInput,true);" +
    "post('ready',{});" +
    "})();";

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
    var tag = EDIT_STYLE + '<script id="leo-canvas-picker">\n' + FRAME_SCRIPT + "\n</script>";
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
    toolbar.appendChild(pickBtn);
    toolbar.appendChild(editBtn);
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

    function syncInsertBar() {
      insertBar.hidden = !editing;
      wrap.classList.toggle("is-editing", editing);
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
        if (editing) postToFrame({ type: "edit", on: true });
        return;
      }
      if (d.type === "html") {
        htmlDoc = String(d.html || htmlDoc);
        if (onChange) onChange(htmlDoc);
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
          applyImg: function (dataUrl) {
            postToFrame({ type: "setImg", leoId: String(d.leoId || ""), dataUrl: dataUrl });
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
      getHtml: getHtml,
      setSheetId: function (id) {
        sheetId = String(id || "");
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
