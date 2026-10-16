// Trip state stays in this page; each API call is independent.
let tripState = null;
const $ = (id) => document.getElementById(id);
const greeting = $('messages').innerHTML;
let busy = false;
let itinerary = '';
let hasImage = false;
const storageKey = 'safarai-trip-v1';
const draftKey = 'safarai-draft-v1';
let pendingMessage = null;
let lastFailure = '';
function rememberDraft() {
  try { sessionStorage.setItem(draftKey, JSON.stringify({draft: $('message').value, pending: pendingMessage, error: lastFailure, saved: Date.now()})); }
  catch { /* Planning remains usable when browser storage is unavailable. */ }
}
function restoreDraft() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
    if (!saved || Date.now() - saved.saved > 86400000) return;
    if (typeof saved.draft === 'string') $('message').value = saved.draft.slice(0, 6000);
    pendingMessage = typeof saved.pending === 'string' ? saved.pending.slice(0, 6000) : null;
    lastFailure = typeof saved.error === 'string' ? saved.error.slice(0, 1500) : '';
  } catch { /* Malformed browser data is ignored, never rendered as HTML. */ }
}
function showPendingMessage() {
  if (pendingMessage) {
    addMessage(pendingMessage, 'user');
    addMessage(lastFailure || 'This message was not confirmed as completed. Your draft is ready to retry.', 'error');
  }
}
function rememberTrip() {
  try { sessionStorage.setItem(storageKey, JSON.stringify({state: tripState, saved: Date.now()})); }
  catch { $('status').textContent = 'Trip could not be saved in this browser.'; }
}

// Keep requests sequential and show progress beside the input.
async function withProgress(label, action, showPlanLoading = false) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(button => button.disabled = true);
  $('message').disabled = true;
  $('status').textContent = label;
  document.querySelector('.chat-panel').setAttribute('aria-busy', 'true');
  let loadingReminder;
  if (showPlanLoading) {
    $('plan-loading-title').textContent = itinerary ? 'Working on your changes...' : 'Working on your adventure...';
    $('plan-loading-detail').textContent = itinerary
      ? 'Your current itinerary stays available while we prepare a reply.'
      : 'We are preparing your reply. Detailed itineraries can take a little longer.';
    $('plan-loading').hidden = false;
    $('empty-plan').hidden = true;
    document.querySelector('.plan-panel').setAttribute('aria-busy', 'true');
    loadingReminder = setTimeout(() => {
      $('plan-loading-detail').textContent = 'Still working on your request. You can leave this tab open while it finishes.';
    }, 15000);
  }

  try {
    await action();
  } catch (error) {
    addMessage(error.message, 'error');
  } finally {
    clearTimeout(loadingReminder);
    if (showPlanLoading) {
      $('plan-loading').hidden = true;
      $('empty-plan').hidden = Boolean(itinerary);
      document.querySelector('.plan-panel').setAttribute('aria-busy', 'false');
    }
    busy = false;
    document.querySelectorAll('button').forEach(button => button.disabled = false);
    $('image-button').disabled = hasImage;
    $('message').disabled = false;
    $('status').textContent = 'Ready to explore';
    document.querySelector('.chat-panel').setAttribute('aria-busy', 'false');
    if (!window.matchMedia('(max-width: 800px)').matches) $('message').focus();
  }
}

function addMessage(content, kind = 'assistant') {
  const message = document.createElement('article');
  message.className = `message ${kind}`;

  const avatar = document.createElement('span');
  avatar.className = 'avatar';
  avatar.textContent = kind === 'user' ? 'You' : '\u2708';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  // Assistant HTML is sanitized by the server. Other text is never HTML.
  if (kind === 'assistant') bubble.innerHTML = content;
  else bubble.textContent = content;

  message.append(avatar, bubble);
  $('messages').append(message);
  $('messages').scrollTop = $('messages').scrollHeight;
}

async function request(action, body = {}) {
  let response;
  try {
    response = await fetch(`/api/${action}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Travel-Request': '1'},
      body: JSON.stringify({...body, state: tripState}),
    });
  } catch {
    throw new Error('Could not reach the server. Check your connection and retry; your message is saved in this tab.');
  }
  if (!response.headers.get('Content-Type')?.includes('application/json')) {
    if ([408, 504].includes(response.status)) throw new Error('The server took too long to respond. Your message is kept so you can retry.');
    if ([401, 403].includes(response.status)) throw new Error('Sign-in is required or access was denied. Refresh to sign in; your draft is saved in this tab.');
    throw new Error('The server is unavailable or requires sign-in. Refresh and try again.');
  }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The server returned an unreadable response. Your message is kept so you can retry.'); }
  if (data.state) { tripState = data.state; rememberTrip(); }
  if (data.usage_html) $('usage').innerHTML = data.usage_html;
  if (!response.ok) {
    const fallback = [408, 504].includes(response.status) ? 'The server took too long to respond. Please retry.' : 'The request failed. Please retry.';
    throw new Error((typeof data.error === 'string' ? data.error : fallback) + (data.request_id ? ` Reference: ${data.request_id}` : ''));
  }
  return data;
}

function clearImage() {
  hasImage = false;
  $('image-wrap').hidden = true;
  $('destination-image').removeAttribute('src');
  $('image-button').textContent = 'Add a destination image';
}

// Chat and quick-start suggestions.
$('chat-form').addEventListener('submit', event => {
  event.preventDefault();
  const message = $('message').value.trim();
  if (!message) return;

  withProgress('Finding your way...', async () => {
    addMessage(message, 'user');
    $('starters').hidden = true;
    pendingMessage = message;
    lastFailure = '';
    rememberDraft();
    let data;
    try {
      data = await request('chat', {message});
    } catch (error) {
      lastFailure = error.message;
      rememberDraft();
      throw error;
    }
    $('message').value = '';
    pendingMessage = null;
    lastFailure = '';
    rememberDraft();

    if (!data.itinerary) {
      addMessage(data.reply);
      return;
    }
    addMessage('<p>Your adventure plan is ready! Tell me what you would like to change.</p>');
    itinerary = data.itinerary;
    $('itinerary').innerHTML = data.itinerary_html;
    $('plan-heading').textContent = `Your ${data.destination || 'next'} adventure`;
    $('empty-plan').hidden = true;
    $('plan-content').hidden = false;
    clearImage();
    // The itinerary sits below chat on phones, so bring the new plan into view.
    if (window.matchMedia('(max-width: 800px)').matches) {
      document.querySelector('.plan-panel').scrollIntoView({block: 'start'});
    }
  }, true);
});

$('message').addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $('chat-form').requestSubmit();
  }
});

for (const button of document.querySelectorAll('[data-prompt]')) {
  button.addEventListener('click', () => {
    $('message').value = button.dataset.prompt;
    rememberDraft();
    $('message').focus();
  });
}

// Reset the signed conversation and the displayed trip together.
$('reset').addEventListener('click', () => {
  withProgress('Starting fresh...', async () => {
    tripState = null;
    pendingMessage = null;
    lastFailure = '';
    try { sessionStorage.removeItem(storageKey); sessionStorage.removeItem(draftKey); } catch {}
    $('usage').textContent = 'No requests yet.';
    itinerary = '';
    $('messages').innerHTML = greeting;
    $('message').value = '';
    $('starters').hidden = false;
    $('empty-plan').hidden = false;
    $('plan-content').hidden = true;
    $('itinerary').innerHTML = '';
    $('plan-heading').textContent = 'Your next adventure';
    clearImage();
  });
});

// Images are optional and retained in this page for the current plan.
$('image-button').addEventListener('click', () => {
  if (hasImage) return;
  withProgress('Creating a little inspiration...', async () => {
    const data = await request('image');
    $('destination-image').src = data.image;
    $('image-wrap').hidden = false;
    hasImage = true;
    $('image-button').textContent = 'Image added';
  });
});

// Use the browser's PDF export; no extra library or API request is needed.
$('download').addEventListener('click', () => {
  if (!itinerary) return;
  window.print();
});

// Restore only server-verified signed state; never trust stored HTML.
async function restoreTrip() {
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(storageKey) || 'null'); } catch { showPendingMessage(); return; }
  if (!saved?.state) { showPendingMessage(); return; }
  if (Date.now() - saved.saved > 3600000) {
    $('status').textContent = 'Saved trip expired. Start a new trip.';
    try { sessionStorage.removeItem(storageKey); } catch {}
    showPendingMessage();
    return;
  }
  tripState = saved.state;
  await withProgress('Restoring your trip...', async () => {
    try {
      const data = await request('restore');
      $('messages').innerHTML = greeting;
      for (const message of data.messages || []) {
        const wrapper = document.createElement('div'); wrapper.innerHTML = message.html;
        addMessage(message.role === 'user' ? wrapper.textContent : message.html, message.role);
      }
      $('starters').hidden = Boolean(data.messages?.length);
      if (data.itinerary) {
        itinerary = data.itinerary;
        $('itinerary').innerHTML = data.itinerary_html;
        $('plan-heading').textContent = `Your ${data.destination || 'next'} adventure`;
        $('empty-plan').hidden = true; $('plan-content').hidden = false;
      }
    } catch (error) {
      // Keep the active token too: a message sent after a transient restore
      // failure must continue this trip rather than silently start a new one.
      throw new Error('Could not restore your saved trip. Refresh to retry, or choose New trip. ' + error.message);
    }
  });
  showPendingMessage();
}
$('message').addEventListener('input', rememberDraft);
restoreDraft();
restoreTrip();
