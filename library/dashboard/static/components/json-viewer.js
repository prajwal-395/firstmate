/**
 * json-viewer.js - A collapsible tree viewer for JSON data.
 */

function createJsonViewer(data) {
    const container = document.createElement('div');
    container.className = 'json-viewer-container';
    
    // Add copy button
    const copyBtn = document.createElement('button');
    copyBtn.className = 'btn btn-ghost text-sm mb-2';
    copyBtn.textContent = 'Copy JSON';
    copyBtn.onclick = () => {
        navigator.clipboard.writeText(JSON.stringify(data, null, 2));
        copyBtn.textContent = 'Copied!';
        setTimeout(() => copyBtn.textContent = 'Copy JSON', 2000);
    };
    container.appendChild(copyBtn);
    
    const tree = renderNode(data, 'root');
    tree.style.fontFamily = 'var(--font-mono)';
    tree.style.fontSize = '13px';
    container.appendChild(tree);
    
    return container;
}

function renderNode(value, key, isLast = true, depth = 0) {
    const wrapper = document.createElement('div');
    wrapper.style.marginLeft = depth === 0 ? '0' : '16px';
    
    // Primitive types
    if (value === null || typeof value !== 'object') {
        const line = document.createElement('div');
        let html = '';
        if (key !== 'root') {
            html += `<span style="color: var(--accent-cyan)">"${escapeHtml(key)}"</span>: `;
        }
        if (value === null) {
            html += `<span style="color: var(--text-muted)">null</span>`;
        } else if (typeof value === 'string') {
            html += `<span style="color: #3fb950">"${escapeHtml(value)}"</span>`;
        } else if (typeof value === 'number') {
            html += `<span style="color: #d29922">${value}</span>`;
        } else if (typeof value === 'boolean') {
            html += `<span style="color: var(--accent-purple)">${value}</span>`;
        }
        if (!isLast) html += ',';
        line.innerHTML = html;
        wrapper.appendChild(line);
        return wrapper;
    }
    
    // Arrays and Objects
    const isArray = Array.isArray(value);
    const keys = Object.keys(value);
    const isEmpty = keys.length === 0;
    
    const head = document.createElement('div');
    head.style.cursor = isEmpty ? 'default' : 'pointer';
    
    let html = '';
    
    if (key !== 'root') {
        html += `<span style="color: var(--accent-cyan)">"${escapeHtml(key)}"</span>: `;
    }
    
    const openBrace = isArray ? '[' : '{';
    const closeBrace = isArray ? ']' : '}';
    
    if (isEmpty) {
        html += `${openBrace}${closeBrace}${isLast ? '' : ','}`;
        head.innerHTML = html;
        wrapper.appendChild(head);
        return wrapper;
    }
    
    const toggle = document.createElement('span');
    toggle.style.display = 'inline-block';
    toggle.style.width = '12px';
    
    // Collapse arrays with >20 items by default
    const shouldCollapse = isArray && value.length > 20;
    
    toggle.innerHTML = shouldCollapse ? '&#9654;' : '&#9660;';
    toggle.style.fontSize = '10px';
    toggle.style.marginRight = '4px';
    toggle.style.color = 'var(--text-muted)';
    
    const headText = document.createElement('span');
    headText.innerHTML = html + openBrace;
    
    const summary = document.createElement('span');
    summary.style.color = 'var(--text-muted)';
    summary.style.display = shouldCollapse ? 'inline' : 'none';
    summary.textContent = isArray ? ` [${value.length} items] ` : ' ... ';
    
    const closeBracketInline = document.createElement('span');
    closeBracketInline.style.display = shouldCollapse ? 'inline' : 'none';
    closeBracketInline.textContent = `${closeBrace}${isLast ? '' : ','}`;
    
    head.appendChild(toggle);
    head.appendChild(headText);
    head.appendChild(summary);
    head.appendChild(closeBracketInline);
    wrapper.appendChild(head);
    
    const childrenContainer = document.createElement('div');
    childrenContainer.style.display = shouldCollapse ? 'none' : 'block';
    
    keys.forEach((k, i) => {
        const childNode = renderNode(value[k], isArray ? k : k, i === keys.length - 1, depth + 1);
        childrenContainer.appendChild(childNode);
    });
    
    wrapper.appendChild(childrenContainer);
    
    const tail = document.createElement('div');
    tail.style.display = shouldCollapse ? 'none' : 'block';
    tail.textContent = `${closeBrace}${isLast ? '' : ','}`;
    wrapper.appendChild(tail);
    
    head.onclick = (e) => {
        e.stopPropagation();
        const isCollapsed = childrenContainer.style.display === 'none';
        childrenContainer.style.display = isCollapsed ? 'block' : 'none';
        tail.style.display = isCollapsed ? 'block' : 'none';
        summary.style.display = isCollapsed ? 'none' : 'inline';
        closeBracketInline.style.display = isCollapsed ? 'none' : 'inline';
        toggle.innerHTML = isCollapsed ? '&#9660;' : '&#9654;';
    };
    
    return wrapper;
}
