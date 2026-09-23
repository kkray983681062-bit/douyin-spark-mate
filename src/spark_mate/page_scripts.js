// Original read-only adapter for the public Douyin PC page. No private API calls.
// CSS names and SDK field names are page contracts, and may change upstream.
(request) => {
  const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const clean = value => String(value ?? '').replace(/[\s\u200b-\u200d\ufeff]+/g, ' ').trim();
  const pathOf = value => { try { return new URL(value, location.href).pathname; } catch { return ''; } };
  const read = (element, names) => {
    const fiberKey = Object.keys(element).find(k => k.startsWith('__reactFiber$'));
    let cursor = fiberKey ? element[fiberKey] : null;
    for (let count = 0; cursor && count < 12; count++, cursor = cursor.return) {
      for (const name of names) {
        const value = cursor.memoizedProps?.[name];
        if (!value || typeof value !== 'object') continue;
        const older = cursor.alternate?.memoizedProps?.[name];
        if (older && (older.clientId !== value.clientId || older.id !== value.id)) return null;
        return value;
      }
      // Do not continue into another mounted DOM component outside this item.
      const parent = cursor.return?.stateNode;
      if (parent instanceof Element && parent !== element && !element.contains(parent)) break;
    }
    const propsKey = Object.keys(element).find(k => k.startsWith('__reactProps$'));
    const props = propsKey && element[propsKey];
    for (const name of names) if (props?.[name]) return props[name];
    return null;
  };
  const conversation = el => read(el, ['curConversation', 'conversation', 'conversationInfo']);
  const headers = [...document.querySelectorAll('.RightPanelHeaderconvHeader')].filter(visible);
  const current = headers.length === 1 ? conversation(headers[0]) : null;
  const decimal = n => /^\d+$/.test(String(n ?? '')) ? String(n) : '0';
  const watermark = c => [c?.lastMessageIndexV2, c?.maxIndexV2FromServer].reduce(
    (a, b) => BigInt(decimal(b)) > BigInt(a) ? decimal(b) : a, '0');

  if (request.mode === 'friends') {
    return [...document.querySelectorAll('[data-e2e="conversation-item"]')].filter(visible).map(el => {
      const info = conversation(el);
      if (![1, 2].includes(info?.type) || !info?.id) return null;
      const title = el.querySelector('[class$="Itemtitle"]') || el.querySelector('[title]');
      const name = clean(title?.textContent || el.innerText.split('\n')[0]);
      if (!name) return null;
      const img = el.querySelector('img');
      const flame = el.querySelector('[class*="StreaknormalText"]');
      el.setAttribute('data-spark-key', String(info.id));
      return {key: String(info.id), name, avatar: img?.currentSrc || img?.src || '',
        streak: clean(flame?.textContent), identity: String(info.id), conversation_type: info.type};
    }).filter(Boolean);
  }

  if (request.mode === 'stickers') {
    return [...document.querySelectorAll('.componentsemojiemojiPanel .emojiEmojiItememojiItem')]
      .filter(visible).map(el => {
        const data = read(el, ['sticker']);
        if (!data?.display_name || !data.resource_type) return null;
        const name = String(data.display_name);
        const image = data.static_url || data.animate_url || el.querySelector('img')?.src || '';
        const resource = pathOf(image);
        if (!resource) return null;
        const key = name + '|' + resource;
        el.setAttribute('data-spark-sticker', key);
        return {name, resource, image, key, category: ''};
      }).filter(Boolean);
  }

  if (!current?.id || ![1, 2].includes(current.type)) return {conversation: null, messages: []};
  const knownBoundary = [current.lastMessageIndexV2, current.maxIndexV2FromServer]
    .some(n => /^\d+$/.test(String(n ?? '')));
  const result = {conversation: {id: String(current.id), type: current.type, watermark: watermark(current),
    knownBoundary}, messages: []};
  const payload = request.message || {};
  const ids = new Set();
  const boxes = document.querySelectorAll('.MessageItemTextcontainer, .MessageItemEmojiemojiBox, [class*="MessageItemImage"]');
  for (const box of boxes) {
    if (!visible(box)) continue;
    const msg = read(box, ['message']);
    if (!msg || msg.isFromMe !== true || msg.visible === false || msg.isRecalled || msg.isRefMessage) continue;
    if (String(msg.conversationId) !== String(current.id) || !msg.clientId || ids.has(msg.clientId)) continue;
    if (msg.conversationType !== undefined && msg.conversationType !== current.type) continue;
    const content = msg.parsedContent || {};
    let matches = false;
    if (payload.kind === 'text') matches = msg.type === 7 && clean(content.text) === clean(payload.value);
    if (payload.kind === 'sticker') {
      const urls = [content.url?.uri, ...(content.url?.url_list || [])].filter(Boolean);
      matches = msg.type === 5 && content.display_name === payload.value &&
        (!payload.resource || urls.some(url => pathOf(url) === payload.resource));
    }
    if (payload.kind === 'image') matches = msg.type === 2;
    if (!matches) continue;
    ids.add(msg.clientId);
    result.messages.push({id: String(msg.clientId), server: String(msg.serverId || '0'),
      order: decimal(msg.indexInConversationV2), status: msg.flightStatus ?? null,
      hydrated: msg.flightStatus === undefined && msg.isOffline === false && msg.serverStatus === 0});
  }
  return result;
}
