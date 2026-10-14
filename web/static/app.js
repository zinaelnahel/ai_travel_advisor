// Page state. The server stores the actual conversation.
const $ = (id) => document.getElementById(id);
const greeting = $('messages').innerHTML;
let busy = false;
let itinerary = '';
let hasImage = false;

// Keep requests sequential and show progress beside the input.
async function withProgress(label, action) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(button => button.disabled = true);
  $('message').disabled = true;
  $('status').textContent = label;
  document.querySelector('.chat-panel').setAttribute('aria-busy', 'true');

  try {
    await action();
  } catch (error) {
    addMessage(error.message, 'error');
  } finally {
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
  const response = await fetch(`/api/${action}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Travel-Request': '1'},
    body: JSON.stringify(body),
  });
  if (!response.headers.get('Content-Type')?.includes('application/json')) {
    throw new Error('The server is unavailable or requires sign-in. Refresh and try again.');
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Please try again.');
  if (data.usage_html) $('usage').innerHTML = data.usage_html;
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
    const data = await request('chat', {message});
    $('message').value = '';

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
  });
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
    $('message').focus();
  });
}

// Clear the server conversation and the displayed trip together.
$('reset').addEventListener('click', () => {
  withProgress('Starting fresh...', async () => {
    await request('reset');
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

// Images are optional; the server reuses the image for the current plan.
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
