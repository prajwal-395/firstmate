// message-feed.js

async function renderMessagesView() {
    const content = document.getElementById('messages-content');
    if (!content) return;
    
    try {
        const messages = await api('/messages');
        if (!messages.length) {
            content.innerHTML = '<div class="timeline-empty">No messages from agents.</div>';
            return;
        }
        
        let html = '<div class="message-feed">';
        for (const msg of messages) {
            html += renderMessageCard(msg);
        }
        html += '</div>';
        
        content.innerHTML = html;
        
        // Add unread badge if pending messages
        const pendingCount = messages.filter(m => m.requires_response && !m.responded_at).length;
        const badge = document.getElementById('messages-badge');
        if (badge) {
            badge.textContent = pendingCount;
            badge.style.display = pendingCount > 0 ? 'inline-block' : 'none';
        }
    } catch (err) {
        content.innerHTML = `<div class="timeline-empty">Error loading messages: ${err.message}</div>`;
    }
}

function renderMessageCard(msg) {
    const isPending = msg.requires_response && !msg.responded_at;
    let cardClass = 'message-card';
    if (isPending) cardClass += ' pending';
    
    let html = `
        <div class="${cardClass}" id="msg-${msg.id}">
            <div class="message-header">
                <span class="message-type badge-${msg.type}">${msg.type.toUpperCase()}</span>
                <span class="message-time">${new Date(msg.created_at).toLocaleString()}</span>
            </div>
            <h3 class="message-title">${escapeHtml(msg.title)}</h3>
            <div class="message-body inspector-md">${markdownToHtml(msg.body)}</div>
    `;
    
    if (msg.preview_data && msg.preview_data.thumbnail_url) {
        html += `<img src="${msg.preview_data.thumbnail_url}" class="message-preview-img" alt="Preview">`;
    }
    
    if (msg.options && msg.options.length > 0) {
        html += `<div class="message-options">`;
        for (const opt of msg.options) {
            html += `
                <div class="message-option">
                    ${opt.thumbnail_url ? `<img src="${opt.thumbnail_url}" class="message-option-thumb">` : ''}
                    <div class="message-option-info">
                        <strong>${escapeHtml(opt.label)}</strong>
                        <div>${escapeHtml(opt.description)}</div>
                    </div>
                </div>
            `;
        }
        html += `</div>`;
    }
    
    if (isPending) {
        html += `<div class="message-actions">`;
        if (msg.type === 'decision') {
            html += `
                <button class="btn btn-approve" onclick="respondToMessage('${msg.id}', 'approve')">&#10003; Approve</button>
                <button class="btn btn-reject" onclick="respondToMessage('${msg.id}', 'reject')">&#10007; Reject</button>
                <button class="btn btn-revise" onclick="respondToMessage('${msg.id}', 'revise')">&#9998; Revise</button>
            `;
        } else if (msg.type === 'question') {
            html += `
                <input type="text" id="reply-${msg.id}" class="message-input" placeholder="Type your answer...">
                <button class="btn btn-primary" onclick="respondToMessage('${msg.id}', 'comment')">Send</button>
            `;
        }
        html += `</div>`;
    } else if (msg.responded_at) {
        html += `<div class="message-response-info">
            Responded at ${new Date(msg.responded_at).toLocaleString()} 
            (${msg.response?.action || 'replied'})
        </div>`;
    }
    
    html += `</div>`;
    return html;
}

async function respondToMessage(msgId, action) {
    let comment = '';
    const input = document.getElementById(`reply-${msgId}`);
    if (input) comment = input.value;
    
    try {
        await apiPost(`/messages/${msgId}/respond`, {
            message_id: msgId,
            action: action,
            comment: comment
        });
        await renderMessagesView();
        // Also refresh pipeline data if a gate was resolved
        await refreshData();
    } catch (err) {
        alert(`Error responding to message: ${err.message}`);
    }
}
