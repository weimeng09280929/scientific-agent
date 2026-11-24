/* Local-only Markdown + TeX preview. Never writes back to research records. */
(() => {
    "use strict";
    const records = new WeakMap();
    const escape = text => String(text).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    // Deliberately exclude require/autoload/html: document TeX cannot load code or URLs.
    window.MathJax = {
        startup: {typeset: false},
        tex: {packages: ["base", "ams", "newcommand"], maxBuffer: 20000, maxMacros: 1000},
        svg: {fontCache: "none"},
        options: {enableMenu: false, enableAssistiveMml: false},
    };

    function protectMath(source) {
        const tokens = [], prefix = `sra-${crypto.randomUUID()}-`;
        let output = "", i = 0;
        const escaped = pos => { let n=0; while(pos>0 && source[--pos]==="\\") n++; return n%2===1; };
        while (i < source.length) {
            // Keep fenced and inline code literal, including TeX examples inside it.
            if ((i===0 || source[i-1]==="\n") && /^ {0,3}(`{3,}|~{3,})/.test(source.slice(i))) {
                const fence = source.slice(i).match(/^ {0,3}(`{3,}|~{3,})/)[1];
                const lineEnd = source.indexOf("\n", i);
                const closing = new RegExp(`^ {0,3}${fence[0]}{${fence.length},}[^\\S\\n]*$`, "gm");
                closing.lastIndex = lineEnd < 0 ? source.length : lineEnd+1;
                const end = closing.exec(source);
                const stop = end ? end.index+end[0].length : source.length;
                output += source.slice(i, stop); i=stop; continue;
            }
            if (source[i]==="`" && !escaped(i)) {
                const ticks=source.slice(i).match(/^`+/)[0], end=source.indexOf(ticks,i+ticks.length);
                if(end>=0) {output+=source.slice(i,end+ticks.length);i=end+ticks.length;continue;}
            }
            let open="", close="", display=false;
            if (!escaped(i)) {
                if(source.startsWith("\\(",i)) {open="\\(";close="\\)";}
                else if(source.startsWith("\\[",i)) {open="\\[";close="\\]";display=true;}
                else if(source.startsWith("$$",i)) {open=close="$$";display=true;}
                else if(source[i]==="$" && !/\s/.test(source[i+1] || " ")) {open=close="$";}
            }
            if(open) {
                let end=source.indexOf(close,i+open.length);
                while(end>=0 && escaped(end)) end=source.indexOf(close,end+close.length);
                const tex=end>=0 ? source.slice(i+open.length,end) : "";
                if(tex && (display || !tex.includes("\n")) && (open!=="$" || !/\s$/.test(tex))) {
                    const id=prefix+tokens.length;
                    tokens.push({id,tex,display,raw:source.slice(i,end+close.length)});
                    output+=(display ? "\n\n" : "")+`<span data-sra-math="${id}"></span>`+(display ? "\n\n" : "");
                    i=end+close.length;continue;
                }
            }
            output+=source[i++];
        }
        return {markdown:output,tokens};
    }

    async function render(node, source) {
        const record={source:String(source || ""),sourceMode:false}; records.set(node,record);
        node.classList.add("research-document");
        if (!window.marked || !window.DOMPurify) {node.textContent=record.source;node.classList.add("document-source");return;}
        const {markdown,tokens}=protectMath(record.source);
        node.classList.remove("document-source");
        node.innerHTML=DOMPurify.sanitize(marked.parse(markdown,{gfm:true,breaks:false}), {
            ALLOWED_TAGS:["p","br","hr","strong","em","del","h1","h2","h3","h4","h5","h6","blockquote","ul","ol","li","pre","code","table","thead","tbody","tr","th","td","a","span"],
            ALLOWED_ATTR:["href","title","start","align","data-sra-math"],
            ALLOW_DATA_ATTR:false,
        });
        node.querySelectorAll("a").forEach(a=>{a.rel="noopener noreferrer";a.target="_blank";});
        const slots=tokens.map(token=>{
            const slot=node.querySelector(`[data-sra-math="${token.id}"]`);
            if(slot) {slot.className=token.display ? "document-math document-math-display" : "document-math";slot.textContent=token.raw;}
            return {token,slot};
        });
        record.ready=(async()=>{
            try {
                if(!window.MathJax.startup?.promise) throw new Error("公式排版组件未加载");
                await MathJax.startup.promise;
                for(const {token,slot} of slots) {
                    if(records.get(node)!==record || record.sourceMode) return;
                    if(!slot) continue;
                    try {
                        const math=MathJax.tex2svg(token.tex,{display:token.display});
                        if(math.querySelector('[data-mml-node="merror"]')) throw new Error("公式语法需要核对");
                        // Direct SVG conversion must not display an unstyled duplicate MathML tree.
                        math.querySelectorAll("mjx-assistive-mml").forEach(extra=>extra.remove());
                        slot.replaceChildren(math); slot.setAttribute("aria-label",token.tex);
                    } catch (_) {
                        slot.textContent=token.raw;slot.classList.add("document-math-error");
                        slot.title="这条公式暂未排版，请核对原始公式；原文已保留。";
                    }
                }
            } catch (_) {
                slots.forEach(({slot})=>{if(slot){slot.classList.add("document-math-error");slot.title="公式排版未就绪，原文已保留。";}});
            }
        })();
        await record.ready;
    }

    function toggleSource(node) {
        const record=records.get(node); if(!record)return;
        if(record.sourceMode) {render(node,record.source);return false;}
        record.sourceMode=true;node.classList.add("document-source");node.textContent=record.source;return true;
    }
    const exportStyle="body{max-width:1000px;margin:32px auto;padding:0 24px;color:#172334;font:16px/1.8 system-ui,'Microsoft YaHei',sans-serif}table{border-collapse:collapse;width:100%;margin:1em 0}th,td{border:1px solid #bcc6d1;padding:8px;text-align:left}pre{white-space:pre-wrap;background:#f5f7fa;padding:16px}svg{max-width:100%}.document-math-display{display:block;text-align:center;margin:1em 0}.document-math-error{font-family:monospace;white-space:pre-wrap;color:#8a4d10}h1,h2,h3{line-height:1.4} @media print{body{margin:0;max-width:none}button{display:none}tr,svg{break-inside:avoid}}";
    async function exportHTML(node,title="科研文档") {
        const record=records.get(node);if(!record)throw new Error("请先查看文档");
        const preview=document.createElement("div"); await render(preview,record.source);
        return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>${escape(title)}</title><style>${exportStyle}</style><body>${preview.innerHTML}</body></html>`;
    }
    async function download(node,title) {
        const html=await exportHTML(node,title),url=URL.createObjectURL(new Blob([html],{type:"text/html;charset=utf-8"}));
        const a=document.createElement("a");a.href=url;a.download="research-document.html";a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
    }
    async function print(node,title) {
        const popup=window.open("","_blank");if(!popup)throw new Error("请允许打开打印窗口");
        popup.document.body.textContent="正在准备公式排版…";
        try {const html=await exportHTML(node,title);popup.document.open();popup.document.write(html);popup.document.close();popup.focus();popup.print();}
        catch(error){popup.close();throw error;}
    }
    window.SRA_DOCUMENT={render,toggleSource,download,print,protectMath};
})();
