/* Трёхстороннее слияние по блокам верхнего уровня: правку мимо Yjs (ИИ, бот, старое
 * приложение) — old → new — вносим в текущий документ cur, не трогая остального.
 * Блок, который внешний писатель удалил или заменил, а человек тем временем изменил, —
 * оставляем человеческим (группу пропускаем). Вставки встают после того же соседа. */

function lcsPairs(a, b, eq) {
  var n = a.length;
  var m = b.length;
  var dp = new Array(n + 1);
  for (var i = 0; i <= n; i++) {
    dp[i] = new Int32Array(m + 1);
  }
  for (i = n - 1; i >= 0; i--) {
    for (var j = m - 1; j >= 0; j--) {
      dp[i][j] = eq(a[i], b[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  var pairs = [];
  i = 0;
  j = 0;
  while (i < n && j < m) {
    if (eq(a[i], b[j])) {
      pairs.push([i, j]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      i++;
    } else {
      j++;
    }
  }
  return pairs;
}

/**
 * @param {Array} oldBlocks — база внешнего писателя
 * @param {Array} newBlocks — что он записал
 * @param {Array} curBlocks — текущий совместный документ
 * @param {(x, y) => boolean} eq
 * @returns {Array} блоки результата (элементы cur и new)
 */
export function mergeBlocks(oldBlocks, newBlocks, curBlocks, eq) {
  var anchors = lcsPairs(oldBlocks, newBlocks, eq);
  var oldToCur = {};
  lcsPairs(oldBlocks, curBlocks, eq).forEach(function (p) {
    oldToCur[p[0]] = p[1];
  });

  var deleted = {}; // индекс cur → true
  var inserts = {}; // индекс cur, после которого вставить (-1 — в начало) → [блоки new]

  var curMatched = {};
  Object.keys(oldToCur).forEach(function (k) {
    curMatched[oldToCur[k]] = true;
  });

  /** Позиция в cur, соответствующая концу old[oldIdx]: ближайший совпавший сосед сверху,
   * плюс столько же изменённых человеком блоков за ним, сколько несовпавших в old. */
  function curPosBefore(oldIdx) {
    var k = oldIdx;
    while (k >= 0 && oldToCur[k] === undefined) k--;
    var pos = k >= 0 ? oldToCur[k] : -1;
    var steps = oldIdx - k;
    while (steps > 0 && pos + 1 < curBlocks.length && !curMatched[pos + 1]) {
      pos++;
      steps--;
    }
    return pos;
  }

  var prevOld = -1;
  var prevNew = -1;
  var bounds = anchors.concat([[oldBlocks.length, newBlocks.length]]);
  bounds.forEach(function (anchor) {
    var delFrom = prevOld + 1;
    var delTo = anchor[0];
    var insFrom = prevNew + 1;
    var insTo = anchor[1];
    prevOld = anchor[0];
    prevNew = anchor[1];
    if (delFrom >= delTo && insFrom >= insTo) return;
    var dels = [];
    for (var d = delFrom; d < delTo; d++) {
      if (oldToCur[d] === undefined) return; // человек изменил удаляемый блок — оставляем его версию
      dels.push(oldToCur[d]);
    }
    dels.forEach(function (c) {
      deleted[c] = true;
    });
    if (insFrom >= insTo) return;
    // Замена — на место первого удалённого; иначе — после ближайшего сохранившегося соседа сверху.
    var at = dels.length ? Math.min.apply(null, dels) - 1 : curPosBefore(delFrom - 1);
    var list = inserts[at] || (inserts[at] = []);
    for (var s = insFrom; s < insTo; s++) list.push(newBlocks[s]);
  });

  var out = (inserts[-1] || []).slice();
  for (var c = 0; c < curBlocks.length; c++) {
    if (!deleted[c]) out.push(curBlocks[c]);
    if (inserts[c]) out.push.apply(out, inserts[c]);
  }
  return out;
}
