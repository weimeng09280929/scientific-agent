/* Server-owned limits; this check only avoids needless file reads in the UI. */
(() => {
    let policy;
    const limits = () => policy || (policy = fetch('/api/resource-limits', {cache:'no-store'}).then(async r => {
        if (!r.ok) throw new Error('暂时无法核对上传限制，请稍后手动重试；文件尚未发送。');
        return r.json();
    }).catch(e => {policy = null; throw e;}));
    const description = p => `每次 1 个文件，最大 ${p.upload_file_bytes >= 1048576 ? Number((p.upload_file_bytes / 1048576).toFixed(3))+' MiB' : Number((p.upload_file_bytes / 1024).toFixed(3))+' KiB'}（${p.upload_file_bytes.toLocaleString()} 字节）。繁忙时请稍后手动重试；刷新后须重新选择文件，可核对原上传回执。`;
    window.SRA_RESOURCES = {
        async checkFile(file) {
            const p = await limits();
            if (file && file.size > p.upload_file_bytes) throw new Error(`文件超过大小限制。${description(p)}原文字和已有附件保留。`);
        },
        hint(node) {node.textContent = '正在核对上传大小限制…'; limits().then(p => {node.textContent = description(p);}).catch(e => {node.textContent = e.message;});}
    };
})();
