// Devin Chat Aggregator — front-end controller
(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);

  const state = {
    chats: [],
    accounts: [],
    activeChatId: null,
    pendingAttachment: null, // {url, filename}
    pollTimer: null,
  };

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    return fetch(path, opts).then(async (r) => {
      if (r.status === 401) { window.location.href = '/login'; throw new Error('unauthorized'); }
      const ct = r.headers.get('content-type') || '';
      const data = ct.includes('application/json') ? await r.json() : await r.text();
      if (!r.ok) {
        const msg = (data && data.detail) || (data && data.error) || ('HTTP ' + r.status);
        throw new Error(msg);
      }
      return data;
    });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function renderInline(text) {
    const esc = escapeHtml(text);
    // simple ![alt](url) -> <img>
    return esc.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (_, alt, url) {
      return '<img alt="' + alt + '" src="' + url + '" />';
    }).replace(/\n/g, '<br/>');
  }

  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleString();
  }

  // ------------ chat list ----------------
  function renderChatList() {
    const el = $('chat-list');
    if (!state.chats.length) { el.innerHTML = '<div class="empty-state" style="margin-top:30px"><p class="muted">No chats yet.</p></div>'; return; }
    el.innerHTML = state.chats.map(function (c) {
      const active = c.id === state.activeChatId ? 'active' : '';
      const status = c.status ? '<span class="ci-status">' + escapeHtml(c.status) + '</span>' : '';
      return (
        '<div class="chat-item ' + active + '" data-id="' + c.id + '">' +
        '<div style="flex:1;min-width:0">' +
          '<div class="ci-title">' + escapeHtml(c.title || 'Untitled') + '</div>' +
          '<div class="ci-meta">' + fmtTime(c.updated_at) + '</div>' +
        '</div>' +
        status +
        '<button class="ci-del" data-del="' + c.id + '" title="Delete">×</button>' +
        '</div>'
      );
    }).join('');
    el.querySelectorAll('.chat-item').forEach(function (n) {
      n.addEventListener('click', function (ev) {
        if (ev.target && ev.target.matches('[data-del]')) return;
        selectChat(parseInt(n.getAttribute('data-id'), 10));
      });
    });
    el.querySelectorAll('[data-del]').forEach(function (b) {
      b.addEventListener('click', async function (ev) {
        ev.stopPropagation();
        const id = parseInt(b.getAttribute('data-del'), 10);
        if (!confirm('Delete this chat? This cannot be undone.')) return;
        try {
          await api('/api/chats/' + id, { method: 'DELETE' });
          if (state.activeChatId === id) { state.activeChatId = null; clearMessages(); }
          await loadChats();
        } catch (e) { alert(e.message); }
      });
    });
  }

  function clearMessages() {
    $('messages').innerHTML = '<div class="empty-state"><h3>Welcome.</h3><p>Pick a chat on the left or start a new one. Everything you do here is stored in this site\'s database, so switching Devin accounts won\'t lose your history.</p></div>';
    $('chat-title').textContent = 'No chat selected';
    $('chat-meta').textContent = '';
    $('refresh-btn').disabled = true;
    $('export-btn').disabled = true;
    const link = $('open-in-devin'); link.hidden = true; link.removeAttribute('href');
  }

  function renderMessages(chat) {
    $('chat-title').textContent = chat.title || 'Untitled';
    $('chat-meta').textContent = (chat.status || '–') + ' · ' + fmtTime(chat.updated_at) + (chat.devin_session_id ? ' · ' + chat.devin_session_id : '');
    const link = $('open-in-devin');
    if (chat.devin_url) { link.hidden = false; link.href = chat.devin_url; } else { link.hidden = true; }
    $('refresh-btn').disabled = false;
    $('export-btn').disabled = false;

    const target = $('messages');
    if (!chat.messages || !chat.messages.length) {
      target.innerHTML = '<div class="empty-state"><p>No messages yet. Send the first one.</p></div>';
      return;
    }
    target.innerHTML = chat.messages.map(function (m) {
      const role = m.role || 'system';
      return (
        '<div class="message ' + role + '">' +
          '<div class="role">' + escapeHtml(role) + (m.event_type ? ' · ' + escapeHtml(m.event_type) : '') + '</div>' +
          '<div class="body">' + renderInline(m.content || '') + '</div>' +
        '</div>'
      );
    }).join('');
    target.scrollTop = target.scrollHeight;
  }

  // ------------ data loaders ----------------
  async function loadChats() {
    state.chats = await api('/api/chats');
    renderChatList();
  }

  async function loadAccounts() {
    state.accounts = await api('/api/accounts');
    const sel = $('account-select');
    sel.innerHTML = state.accounts.length
      ? state.accounts.map(function (a) {
          return '<option value="' + a.id + '">' + escapeHtml(a.label) + (a.is_default ? ' ★' : '') + '</option>';
        }).join('')
      : '<option value="">no accounts</option>';
    const def = state.accounts.find(function (a) { return a.is_default; });
    if (def) sel.value = String(def.id);
    renderAccountsList();
  }

  function renderAccountsList() {
    const el = $('accounts-list');
    if (!state.accounts.length) { el.innerHTML = '<p class="muted">No accounts yet. Add one below.</p>'; return; }
    el.innerHTML = state.accounts.map(function (a) {
      return (
        '<div class="account-row">' +
          '<div><strong>' + escapeHtml(a.label) + '</strong> <span class="key">' + escapeHtml(a.key_preview) + '</span></div>' +
          (a.is_default ? '<span class="badge">default</span>' : '<button data-make-default="' + a.id + '" class="ghost">Make default</button>') +
          '<button data-del-acc="' + a.id + '" class="ghost">Delete</button>' +
        '</div>'
      );
    }).join('');
    el.querySelectorAll('[data-make-default]').forEach(function (b) {
      b.addEventListener('click', async function () {
        await api('/api/accounts/' + b.getAttribute('data-make-default') + '/default', { method: 'POST' });
        await loadAccounts();
      });
    });
    el.querySelectorAll('[data-del-acc]').forEach(function (b) {
      b.addEventListener('click', async function () {
        if (!confirm('Delete this Devin account? Existing chats from it will keep their history but cannot be continued.')) return;
        await api('/api/accounts/' + b.getAttribute('data-del-acc'), { method: 'DELETE' });
        await loadAccounts();
      });
    });
  }

  async function selectChat(id) {
    state.activeChatId = id;
    renderChatList();
    const chat = await api('/api/chats/' + id);
    renderMessages(chat);
    schedulePolling(chat);
  }

  function schedulePolling(chat) {
    if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
    if (!chat || state.activeChatId !== chat.id) return;
    const isWorking = !chat.status || ['working', 'running', 'queued'].indexOf(String(chat.status).toLowerCase()) !== -1;
    if (!isWorking) return;
    state.pollTimer = setTimeout(async function () {
      try {
        const updated = await api('/api/chats/' + chat.id + '/refresh', { method: 'POST' });
        if (state.activeChatId === chat.id) {
          renderMessages(updated);
          schedulePolling(updated);
          await loadChats();
        }
      } catch (e) {
        // soft fail; try again later
        schedulePolling(chat);
      }
    }, 5000);
  }

  // ------------ composer ----------------
  $('composer').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    const text = $('prompt').value.trim();
    if (!text && !state.pendingAttachment) return;
    const accountId = parseInt($('account-select').value, 10) || null;
    const modelHint = $('model-select').value || null;
    const attachments = state.pendingAttachment ? [state.pendingAttachment] : [];

    $('send-btn').disabled = true;
    try {
      let chat;
      if (state.activeChatId) {
        chat = await api('/api/chats/' + state.activeChatId + '/messages', {
          method: 'POST',
          body: JSON.stringify({ message: text, attachments: attachments }),
        });
      } else {
        chat = await api('/api/chats', {
          method: 'POST',
          body: JSON.stringify({ message: text, account_id: accountId, model_hint: modelHint, attachments: attachments }),
        });
        state.activeChatId = chat.id;
      }
      $('prompt').value = '';
      state.pendingAttachment = null;
      $('attachment-pill').hidden = true;
      $('attachment-pill').textContent = '';
      $('file-input').value = '';
      renderMessages(chat);
      await loadChats();
      schedulePolling(chat);
    } catch (e) {
      alert('Send failed: ' + e.message);
    } finally {
      $('send-btn').disabled = false;
    }
  });

  $('refresh-btn').addEventListener('click', async function () {
    if (!state.activeChatId) return;
    try {
      const chat = await api('/api/chats/' + state.activeChatId + '/refresh', { method: 'POST' });
      renderMessages(chat);
      schedulePolling(chat);
      await loadChats();
    } catch (e) { alert(e.message); }
  });

  $('new-chat-btn').addEventListener('click', function () {
    state.activeChatId = null;
    renderChatList();
    clearMessages();
    $('prompt').focus();
  });

  $('file-input').addEventListener('change', async function () {
    const f = this.files && this.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append('file', f);
    const accountId = $('account-select').value;
    if (accountId) fd.append('account_id', accountId);
    const pill = $('attachment-pill');
    pill.hidden = false;
    pill.textContent = '⇪ uploading ' + f.name;
    try {
      const r = await fetch('/api/standalone/attachments', { method: 'POST', body: fd });
      if (r.status === 401) { window.location.href = '/login'; return; }
      const data = await r.json();
      if (!r.ok) throw new Error((data && data.detail) || 'upload failed');
      // try to extract a URL from the upload payload
      const upload = (data && data.upload) || {};
      const url = upload.url || upload.attachment_url || upload.download_url || (upload.attachment && upload.attachment.url) || null;
      if (!url) throw new Error('no URL in upload response (got: ' + JSON.stringify(upload).slice(0, 200) + ')');
      state.pendingAttachment = { url: url, filename: f.name };
      pill.textContent = '📎 ' + f.name;
    } catch (e) {
      pill.hidden = true;
      pill.textContent = '';
      alert('Upload failed: ' + e.message);
    }
  });

  // ------------ settings modal ----------------
  function openModal(id) { $(id).hidden = false; }
  function closeModal(id) { $(id).hidden = true; }

  document.querySelectorAll('[data-close]').forEach(function (n) {
    n.addEventListener('click', function () { closeModal(n.getAttribute('data-close')); });
  });

  $('settings-btn').addEventListener('click', async function () {
    try {
      const cfg = await api('/api/config');
      const repo = cfg.github_default_repo || '(not set)';
      const token = cfg.github_token_set ? 'configured' : 'not set';
      $('gh-status').textContent = 'Default repo: ' + repo + ' · token: ' + token;
    } catch (e) { /* ignore */ }
    openModal('settings-modal');
  });

  $('add-account-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    const label = $('acc-label').value.trim();
    const key = $('acc-key').value.trim();
    const isDefault = $('acc-default').checked;
    try {
      await api('/api/accounts', {
        method: 'POST',
        body: JSON.stringify({ label: label, api_key: key, is_default: isDefault }),
      });
      $('acc-label').value = '';
      $('acc-key').value = '';
      $('acc-default').checked = false;
      await loadAccounts();
    } catch (e) { alert(e.message); }
  });

  // ------------ export modal ----------------
  $('export-btn').addEventListener('click', function () {
    if (!state.activeChatId) return;
    $('export-msg').textContent = '';
    openModal('export-modal');
  });

  $('export-form').addEventListener('submit', async function (ev) {
    ev.preventDefault();
    if (!state.activeChatId) return;
    const repo = $('exp-repo').value.trim();
    const branch = $('exp-branch').value.trim() || 'main';
    const path = $('exp-path').value.trim();
    $('export-msg').textContent = 'Exporting…';
    try {
      const r = await api('/api/chats/' + state.activeChatId + '/export-github', {
        method: 'POST',
        body: JSON.stringify({ repo: repo, branch: branch, path: path }),
      });
      $('export-msg').innerHTML = 'Exported to <a href="' + r.html_url + '" target="_blank" rel="noopener">' + escapeHtml(r.path) + '</a>';
    } catch (e) {
      $('export-msg').innerHTML = '<span style="color:var(--error)">' + escapeHtml(e.message) + '</span>';
    }
  });

  // ------------ boot ----------------
  (async function () {
    try {
      await loadAccounts();
      await loadChats();
    } catch (e) {
      console.error(e);
    }
  })();
})();
